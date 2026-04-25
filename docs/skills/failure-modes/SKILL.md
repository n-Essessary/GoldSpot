---
name: failure-modes
description: "Registry of known failure modes across GoldSpot — symptom, root cause, fix rule, regression guard. Single source of truth referenced by chat instructions, debug skill, and qa-testing skill. When diagnosing a bug, check here first before reinvestigating. When fixing a new class of bug, add an entry here."
---

# Failure Modes Registry

> **Authority:** This file is the canonical list of known failures. `debug` skill, `qa-testing` skill, and chat instructions reference this file — they must not duplicate entries.
>
> **When you fix a new bug class:** add an entry here before closing the case.
>
> **Last verified:** 2026-04-24

---

## How to use

1. Bug reported → grep symptom here first
2. Entry exists → apply Fix Rule, check Regression Guard passes
3. Entry missing → invoke `debug` skill for root cause analysis, then add entry here
4. Each entry must have all 4 fields: Symptom, Root Cause, Fix Rule, Regression Guard

---

## [parser] G2G returns wrong prices

- **Symptom:** G2G offers show inflated or aggregated prices; `price_per_1k` higher than real marketplace
- **Root cause:** Phase 2 call missing `group=0` parameter → returns grouped aggregates, not individual offers
- **Fix rule:** `group=0` mandatory in all Phase 2 URLs. See `_registry § 5`
- **Regression guard:** manual curl of Phase 2 URL without `group=0` must differ from real cheapest price visible on g2g.com

---

## [parser] G2G low offer count (< 100)

- **Symptom:** G2G parser returns far fewer offers than expected (~30–50 instead of 300+)
- **Root cause:** Phase 1 pagination stopped at page 1 instead of iterating until `len(results) < page_size`
- **Fix rule:** paginate until `len(results) < page_size` or `page > max_pages`. See `_registry § 6`
- **Regression guard:** log `len(unique_pairs)` after Phase 1 — must be ≥ expected_server_count × ~2 (two factions per server)

---

## [parser] G2G `filter_attr` returns 0 results

- **Symptom:** Phase 2 responses are empty for most groups; final offer list is empty or tiny
- **Root cause:** wrong `filter_attr` construction — wrong separator, wrong strip regex, or extra chars
- **Fix rule:** exact formula `og = og.lstrip("/")`, `prefix = re.sub(r"_\d+$", "", og)`, `fa = f"{prefix}:{og}"`. See `_registry § 5`
- **Regression guard:** unit test `_build_filter_attr("lgc_game_27816_lgc_service_1_573_alliance")` returns `"lgc_game_27816_lgc_service_1_573:lgc_game_27816_lgc_service_1_573_alliance"`

---

## [parser] G2G Phase 1 prices used as real prices

- **Symptom:** all G2G prices are group-level aggregates, not individual offers
- **Root cause:** code reads `unit_price_in_usd` from Phase 1 results instead of Phase 2
- **Fix rule:** Phase 1 extracts only `(offer_group, region_id)` pairs. Phase 1 `unit_price_in_usd` is discarded. Prices come from Phase 2 `results[0].unit_price_in_usd` only
- **Regression guard:** code review — Phase 1 loop must not write to any price field

---

## [parser] G2G dual-sort broken (Classic)

- **Symptom:** Classic offer count halved; missing recommended offers OR missing cheapest offers
- **Root cause:** one of the two sort modes (`lowest_price` / `recommended_v2`) removed from `gather()`
- **Fix rule:** both sorts must run concurrently in Classic cycle. See `_registry § 6`
- **Regression guard:** `seller` label distribution in DB must include both `"Lowest Price"` and `"Recommended"`

---

## [parser] FunPay EUR prices treated as USD

- **Symptom:** FunPay prices ~10–15% higher than real USD price
- **Root cause:** Railway EU IP → FunPay returns EUR, code assumed USD
- **Fix rule:** detect currency from `.tc-price .unit`, convert via `open.er-api.com` → `jsdelivr CDN` fallback. `frankfurter.app` blocked on Railway. See `_registry § 7`
- **Regression guard:** log detected currency on every fetch; alert if currency changes unexpectedly

---

## [parser] FunPay price 1000× inflated

- **Symptom:** FunPay `price_per_1k` values around $3000 instead of $3
- **Root cause:** `raw_price_unit` set to `per_unit` instead of `per_lot`
- **Fix rule:** FunPay is `per_lot`, `lot_size = amount_gold`. See `_registry § 10`
- **Regression guard:** test `price_per_1k = (raw_price / lot_size) * 1000` for FunPay fixtures

---

## [parser] `display_server` set in parser

- **Symptom:** `display_server` shows raw title with wrong version/region formatting after normalize pipeline
- **Root cause:** parser set `display_server` field instead of leaving empty — normalize_pipeline overwrites incorrectly or skips
- **Fix rule:** parser leaves `display_server = ""`. `_apply_canonical()` in normalize_pipeline sets it via `make_display_group()`
- **Regression guard:** assert parser output has `display_server == ""` for all offers

---

## [normalize] Unknown version silently defaults to Classic

- **Symptom:** new version (e.g. MoP, Retail) appears as "Classic" in DB and UI
- **Root cause:** `detect_version()` fell through to default branch instead of quarantining
- **Fix rule:** unknown version → log WARNING + quarantine `unknown_version:{value}`. Never return default. See `_registry §§ 1–2`
- **Regression guard:** `detect_version("(EU) Someweirdstring")` must raise or return None, not a canonical value

---

## [db] Alembic broken chain

- **Symptom:** `alembic upgrade head` fails with "multiple heads" or "revision not found"
- **Root cause:** new migration created without checking `alembic heads` first
- **Fix rule:** always run `alembic heads` before creating a new migration. Verify chain linearity before deploy
- **Regression guard:** CI check `alembic heads | wc -l` == 1

---

## [db] `alembic_version` corruption

- **Symptom:** deploy fails with alembic version mismatch between DB and migration files
- **Root cause:** manual migration edits or aborted deploys left `alembic_version` in invalid state
- **Fix rule:** manually correct `alembic_version` table to known good revision. Document action in migration notes
- **Regression guard:** never edit migration files after deploy; use new migration for fixes

---

## [runtime] Railway log spam from resolver

- **Symptom:** Railway logs flooded with connection errors from `server_resolver.py`
- **Root cause:** transient Railway cold-start connection failures retried without backoff → thousands of errors/minute
- **Fix rule:** exponential backoff + circuit breaker mandatory in `server_resolver.py`. See `_registry § 13` for TTL
- **Regression guard:** inject connection failure in test → verify max retries respected, circuit opens

---

## [runtime] Cache wiped on empty parser result

- **Symptom:** after brief parser failure, UI shows empty offers list for 30–60s
- **Root cause:** parser returned `[]` → code cleared cache instead of preserving
- **Fix rule:** if parser returns `[]` but cache populated → keep cache, log WARNING. Only replace cache on successful fetch with results
- **Regression guard:** integration test — parser mocked to return `[]`, cache from previous run must persist

---

## [frontend] Chart right-edge gap

- **Symptom:** price chart shows empty space between last data point and right edge
- **Root cause:** hardcoded `barSpacing/2` offset, or `rightOffset` not set to 0
- **Fix rule:** use pinned now-point on last candle; never hardcode `barSpacing` arithmetic. `rightOffset: 0` + `lastValueVisible: true` + `title` on series
- **Regression guard:** visual check after any chart config change — right edge must touch now-line

---

## [frontend] Chart zoom resets on background refresh

- **Symptom:** user zoomed into chart range; background poll fires → zoom jumps back to default
- **Root cause:** `setData()` resets visible range; no save/restore around update
- **Fix rule:** save `getVisibleLogicalRange()` before `setData`, restore after. Use `fittedRef` flag to distinguish first load from refresh
- **Regression guard:** e2e test — zoom in, wait for poll, assert visible range unchanged

---

## [frontend] UI flickers empty during update

- **Symptom:** brief flash of empty state when new offers arrive
- **Root cause:** `useOffers` clears state before fetching new data
- **Fix rule:** meta-poll every 10s → re-fetch only if `last_update` changed. Never clear state before update completes
- **Regression guard:** DOM snapshot during poll cycle — offers list never empty if previous cycle had data

---

## When to add a new entry

A bug qualifies for this registry if **any** of these are true:
- Took > 30 minutes to diagnose from first symptom
- Has a non-obvious root cause (not just a typo)
- Has a specific fix rule that prevents recurrence
- Has happened more than once
- Crosses skill boundaries (parser ↔ normalize, backend ↔ frontend)

One-off typos, transient infra blips, and fixes without generalizable rules do NOT belong here — they clutter the registry.
