# Case Log — Normalization Pipeline

`normalize_pipeline`, `server_resolver`, alias cache, validation/quarantine logic.

---

## [ARCH] Normalization pipeline structure

**Status:** production
**Files:** `backend/normalize/pipeline.py`, `backend/normalize/server_resolver.py`

### Flow
```
raw offer (from adapter)
  → phase 0: _normalize_{source}_offer()    [display_group string cleanup, source-specific]
  → phase 1: normalize_offer_batch()        [validate → resolve → canonicalize → price-validate → dedup]
  → _cache[source]  +  quarantine ring buffer (for failed offers)
```

### Why a pipeline (not inline normalization in adapter)
- Source-specific quirks isolated in adapter
- All cross-source invariants (canonical entity, price validation, dedup) enforced in one place
- New marketplace = write adapter only, pipeline unchanged

### Invariants
- `display_server` always built via `make_display_group(region, version)` — never hand-assembled
- `realm_type` (Normal/Hardcore) is a separate dimension — never embedded in `display_server`, never a version
- `region` always sourced from canonical registry — never trusted from raw parser data
- `raw_region` preserved alongside canonical `region` for traceability

---

## [BUG] Cache wipe on parser failure caused frontend blank

**Severity:** critical (UX outage)

### Symptom
When a parser cycle returned 0 offers (transient marketplace error), frontend went blank for 30–60s until next successful cycle.

### Root cause
On empty parser result, cache was being cleared. `/offers` endpoint returned `{ offers: [] }`. Frontend rendered empty state.

### Resolution
**Cache resilience rule:**
- Parser returns `[]` + cache populated → keep cache, log warning
- Cache wipe only on explicit invalidation, never on transient adapter failure

```python
if not new_offers and _cache[source]:
    log.warning(f"{source} parser returned empty, keeping {len(_cache[source])} cached offers")
    return  # do not overwrite
```

### Prevention
- Rule in chat instructions: "Cache resilience: parser returns `[]` + cache populated → keep cache"

---

## [BUG] All offers quarantined when alias cache unavailable

**Severity:** critical (full data loss for affected source)

### Symptom
When DB temporarily unreachable for alias lookup, every offer in batch went to quarantine. `/offers` returned empty for affected source.

### Root cause
`server_resolver` treated DB error as "alias not found" and quarantined the offer. With DB down, this nuked the entire batch.

### Resolution
- Cache miss (alias not in DB) → `entity_id=None`, do NOT quarantine
- Cache unavailable (DB error) → bypass resolver entirely, do NOT quarantine all offers
- Distinguish "no match" from "lookup failed"

### Prevention
- Rule in chat instructions: separate handling for cache-miss vs cache-unavailable
- Circuit breaker on resolver (see entry below)

---

## [BUG] Server resolver flooded Railway logs during alias DB outage

**Severity:** medium (log spam, increased Railway costs)

### Symptom
DB hiccup → resolver retried per-offer for every offer in every cycle → thousands of identical error log lines per minute.

### Root cause
No backoff. No circuit breaker. Each offer triggered an independent retry loop.

### Resolution
- Exponential backoff on resolver DB calls
- Circuit breaker: after N consecutive failures, skip resolver entirely for cooldown window
- Single log line per breaker state change, not per offer

### Prevention
- Rule in chat instructions: circuit breaker on `server_resolver.py` mandatory

---

## [BUG] Newly inserted server aliases took up to 60s to take effect

**Severity:** low (annoying during alias backfill, not a regression)

### Root cause
Alias cache TTL is 60s by design. Adding a new alias to DB doesn't invalidate cache.

### Resolution / Workaround
- Accept 60s lag as acceptable for normal operation
- For urgent backfills: redeploy backend (cache rebuild on startup)
- Documented behavior, not a bug to fix

### Notes
Reducing TTL would increase DB load. Active invalidation would require pub/sub. Not worth it for this use case.

---

## [ARCH] Domain enforcement: no silent fallbacks

**Status:** production rule

### Rule
Unknown version OR unknown entity → reject with reason. Never guess. Never default to "Classic" or any specific version.

### Why
Silent fallback masks bugs. A new G2G title format that doesn't match any version regex should LOUDLY fail (WARNING log + quarantine), not silently get tagged as "Classic" and pollute price data.

### Implementation
- `detect_version()` and `detect_realm_type()` have zero pattern overlap
- Unknown → log WARNING, send to quarantine ring buffer with reason
- Quarantine reviewed periodically — known patterns added to detection, junk discarded

### Past violation
`mop classic` alias missing from `version_utils` → titles silently misrouted. Found and fixed by Cowork. Prevention: explicit alias map, reviewed against G2G title corpus on every parser change.

---

## [ARCH] Price profiles for entity rerouting

**Status:** production
**Files:** `backend/normalize/price_profiles.py` (per `available_skills` reference)

### Purpose
In-memory price profile per canonical entity. Used for:
1. Validation: reject offers >10× away from established profile (likely scam or wrong entity)
2. Anomaly detection: flag sudden profile shifts
3. Price-assisted entity rerouting: when alias is ambiguous, use price proximity to disambiguate

### Invariants
- Profile updated only from validated, non-quarantined offers
- Profile bypass for new entities (no historical data → accept everything until profile builds)
