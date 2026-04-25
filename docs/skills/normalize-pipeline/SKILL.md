---
name: normalize-pipeline
description: "Normalization pipeline: validate, resolve, canonicalize, price-validate, and deduplicate offers from any source."
---

# Normalize Pipeline

## Location

`service/normalize_pipeline.py` — called after phase-0 cleanup in every source's background loop.

---

## Pipeline Flow

```
raw offers (from adapter)
  → phase 0: _normalize_{source}_offer()     [offers_service.py]
      — display_group string format cleanup
  → phase 1: normalize_offer_batch()         [normalize_pipeline.py]
      1. validate_offer()      — structural validation
      2. resolve_entity_id()   — canonical registry lookup → entity_id
      3. canonicalize_offer()  — version/category/region canonicalization
      4. price_validate()      — price profile bounds check
      5. dedup()               — source+offer_id uniqueness
  → _cache[source]   (accepted)
  → _quarantine      (rejected)
```

---

## Phase 1: validate_offer()

Reject with quarantine if any condition is true:

| Condition | Quarantine reason |
|---|---|
| `display_server` empty or whitespace | `empty_entity_title` |
| `faction` not in canonical set (see `_registry` § 14) | `unknown_faction:{value}` |
| `price_per_1k <= 0` or `raw_price <= 0` | `zero_price` |
| `amount_gold <= 0` (or quantity ≤ 0) | `zero_amount` |
| `version` in quarantined enum (see `_registry` § 1) | `deprecated_version` |
| Unhandled exception | `pipeline_exception` |

For non-WoW assets: faction validation is asset-schema-specific or skipped.

---

## Phase 2: resolve_entity_id()

Looks up canonical `entity_id` (maps to `server_id` for WoW) via alias cache.

```python
alias_key = _build_alias_key(display_server, server_name, faction, source)
entity_id = alias_cache.get(alias_key)
```

- **Hit** → set `offer.server_id = entity_id`
- **Miss** → `offer.server_id = None` + log to unresolved registry
- **Miss does NOT quarantine** — offer stays in cache without entity_id

### `_build_alias_key()` rules
- Format: `"{display_server}|{server_name}|{faction}|{source}"` — all lowercase+stripped
- Empty `server_name` → empty string (not omitted)
- Source included to allow platform-specific aliases

---

## Phase 3: canonicalize_offer()

**Version aliases (raw → canonical):** see `_registry` → Section 2.
**Canonical version enum:** see `_registry` → Section 1.

Applies version/category alias map to `display_server`:

Pattern: `^\((?P<region>[A-Za-z]{2,})\)\s*(?P<version>.+)$`
- Region → `.upper()`
- Version → `_canonicalize_version(version.strip())` (uses `_VERSION_ALIASES` from `version_utils.py`)
- Result → `offer.display_server = f"({region}) {version}"`

For non-currency assets: extend with asset-type-specific canonicalization.

---

## Phase 4: price_validate()

Uses `service/price_profiles.py` to check price is within expected bounds:

- Profile exists → check `price_per_1k` within `[p10 * 0.1, p90 * 10.0]`
- Price outside bounds → quarantine: `price_out_of_range:{value}`
- No profile (cold start or new entity) → skip validation, accept offer
- Profiles are rebuilt after every parse cycle — lag by one cycle (intentional)

---

## Phase 5: dedup()

Within one parse cycle:
- Key: `(source, offer_id)`
- First occurrence wins
- Duplicate → silently dropped (not quarantined)

---

## Quarantine Contract

```python
@dataclass
class QuarantinedOffer:
    raw_id:    str     # offer.id as produced by adapter
    source:    str     # adapter identifier
    reason:    str     # rejection reason (see phase 1 table)
    raw_title: str     # display_server at time of rejection
    price:     float   # price_per_1k (0 if not computed)
    details:   str     # extra context
    ts:        float   # time.time() at quarantine
```

Ring buffer: `_QUARANTINE_MAX` entries (see `_registry` § 13), oldest dropped first.
Exposed at `GET /admin/quarantine` (admin key required).

---

## detect_version() Rules (WoW)

**Canonical versions and aliases:** see `_registry` → Sections 1–2.
**Source of truth:** `backend/service/version_utils.py::detect_version`

Behavior rules:
- Check patterns in priority order — longer/more specific first (`"season of discovery"` before `"sod"`, `"the war within"` before `"retail"`)
- Quarantined values (e.g. `Season of Mastery`) → reject at enum level
- No match → log WARNING + quarantine `unknown_version:{value}` — **NEVER default silently**

## detect_realm_type() Rules (WoW)

**Canonical realm types:** see `_registry` → Section 3.

```python
r"#hardcore" | r"\bhardcore\b"  → "Hardcore"
# default                       → "Normal"
```

Zero overlap with version detection. `realm_type` is never a version.

---

## Graceful Degradation Rules

- Alias cache unavailable → `entity_id=None`, do NOT quarantine all offers
- Price profiles empty → bypass phase 4, accept offer
- Pipeline crash → log exception, return `(raw_offers, [])` — never crash the loop
- Unknown version after all patterns → quarantine with `unknown_version:{value}`

---

## What Must NOT Happen

- Default unknown version/category to any value silently
- Set `price_per_1k` directly (set `raw_price` + `raw_price_unit`)
- Quarantine offers merely for missing `entity_id` (`None` is valid)
- Block the event loop (all DB calls must be awaited)
- Wipe `_cache` on pipeline exception
