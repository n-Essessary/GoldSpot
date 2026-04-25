---
name: data-science
description: "Price history, trend analysis, and chart data. TradingView-style visualization layer."
---

# Data Science

## Data Sources

**Full endpoint list and response shapes:** see `_registry` → Section 11.

Chart-relevant endpoints:
- `GET /price-history` — per-realm history, params: `last`, `hours`, `faction`
- For `faction=All`: frontend issues two parallel requests (Alliance + Horde) and merges client-side

DB tables backing the endpoints: see `_registry § 8` (tiered storage — `snapshots_1m`, `snapshots_5m`, `snapshots_1h`, `snapshots_1d` with automatic downsampling).

---

## Price Fields

Algorithm constants (`_OUTLIER_MULTIPLIER`, `_MIN_LIQUID_GOLD`, `_VWAP_GOLD_CAP`, `_MIN_OFFERS`): see `_registry § 13`. Algorithm details: see `data-logic` skill.

- `index_price`: Volume-Weighted Median — resilient to outlier volume spikes
- `vwap`: Volume-Weighted Average Price over top offers
- `best_ask`: realistic buy price (cumulative volume ≥ min-liquid threshold)
- `price_min` / `price_max`: raw range after outlier filtering

For charts: `best_ask` for "buy now" line, `index_price` for trend line.

---

## OHLC Bucket Logic

```
bucket_minutes = max(5, (last_hours * 60) // max_points)
```

168h / 500 points → ~20 min buckets. Prevents over-dense charts.

---

## Anomaly Detection

- Spike exceeding outlier multiplier over recent median → flag as suspicious (threshold in `_registry § 13`; service layer already handles)
- Sample size below `_MIN_OFFERS` → data gap, not a real price drop
- New entity with insufficient history → no index, skip in charts

---

## Visualization Guidelines

- Candlestick or line for price history (TradingView-style)
- `best_ask` as primary line, `index_price` as secondary
- Volume bar chart underneath (`total_volume`)
- Time axis: local timezone
- Data gap → connect with dashed line, not zero

---

## Coordination

- Reads from API endpoints only (no direct DB from frontend)
- Per-realm chart params: `server_name + region + version + faction` (or `All` for faction)
- PriceChart frontend invariants (timezone, zoom preservation, faction split, smoothing): see `failure-modes § [frontend]` and `frontend` skill
