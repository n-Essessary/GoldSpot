---
name: data-logic
description: "Price normalization, aggregation, indexing, and filtering. Source-agnostic business logic."
---

# Data Logic

## Core Responsibility

Transform raw offers into meaningful price metrics. All calculations are deterministic and source-agnostic.

---

## Price Normalization (Critical)

**Pricing model table (per_unit / per_lot / flat):** see `_registry` → Section 10.

All comparisons MUST use `price_per_1k` (per 1000 units of currency, USD) — NOT `raw_price`.

**Sorting by `raw_price` directly is wrong.** FunPay `raw_price=3.0` (for 1000g) vs G2G `raw_price=0.003` (per 1g) are not comparable. Always sort by `price_per_1k`.

For non-currency assets: use `unit_price` (price per 1 unit) as the comparable field instead.

---

## IndexPrice Algorithm (Group-Level)

```
1. Sort all offers by price_per_1k
2. Compute raw_median
3. Filter outliers: keep offers where price <= raw_median * _OUTLIER_MULTIPLIER
4. Volume-Weighted Median → index_price
5. VWAP over top offers up to _VWAP_GOLD_CAP → vwap
6. best_ask: price at cumulative_volume >= _MIN_LIQUID_GOLD
```

**Constants (`_OUTLIER_MULTIPLIER`, `_MIN_LIQUID_GOLD`, `_VWAP_GOLD_CAP`, `_MIN_OFFERS`):** see `_registry` → Section 13.

`best_ask` = realistic buy price (used as `min_price` in sidebar).

---

## Entity Index Algorithm (Per-Server, Task 4)

```
1. Filter: same entity_id + faction + price_per_1k > 0
2. Sort by price_per_1k ASC
3. Take top _INDEX_TOP_N cheapest
4. mean_per_1k = mean of top-N price_per_1k values
5. index_price = mean_per_1k / 1000  (stored per-unit in DB)
```

**`_INDEX_TOP_N` value:** see `_registry` → Section 13.

Returns: `{ index_price, sample_size, min_price, max_price }` all in per-unit form.

---

## Filtering Rules

```python
# Group filter (exact match after lowercasing)
_clean(offer.display_server) == _clean(server)

# Entity/realm filter
_clean(offer.server_name) == _clean(server_name)

# Faction filter (case-insensitive)
offer.faction.lower() == faction.lower()
```

`_clean(s)` = collapse whitespace + lowercase. Applied consistently to prevent `(EU) Anniversary` ≠ `(eu)  anniversary`.

---

## Version / Category Sort Order

**Canonical sort order:** see `_registry` → Section 1.

Unknown → rank 99 (sorted last). Used in `get_servers()` for sidebar ordering.

---

## Snapshot Throttle

**Threshold value:** see `_registry` → Section 13 (snapshot throttle).

- Skip DB write if `|new_raw_price - last_raw_price| / last_raw_price <= threshold`
- Prevents ~500k rows/day when prices are stable
- `_last_snap_price: dict[offer_id, float]` tracks last-written price in memory

---

## Anomaly Detection

**Threshold (`_OUTLIER_MULTIPLIER`):** see `_registry` → Section 13.

- `is_suspicious`: offer with `price_per_1k >= group_min * _OUTLIER_MULTIPLIER` → flag ⚠ in UI
- Display-only — suspicious offers remain in cache and DB
- Same multiplier used for outlier exclusion in `compute_index_price`

---

## Rules

- All logic deterministic — no random, no time-dependent results
- `gather(return_exceptions=True)` for parallel writes — always log exceptions
- Empty input → return `None` (not raise, not return 0)
- Minimum 2 offers required for any index calculation (`_MIN_OFFERS=2`)
- Never store `price_per_1k` in DB — derive at read-time from `raw_price`
