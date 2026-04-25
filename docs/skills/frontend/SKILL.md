---
name: frontend
description: "Frontend logic, API integration, state management, and component rules."
---

# Frontend

## Stack

- React + Vite → Vercel: `gold-spot.vercel.app`
- Backend: `VITE_API_URL` env var (never hardcode)
- Key files: `useOffers.js`, `OffersTable.jsx`, `FiltersBar.jsx`, `StatsBar.jsx`, `ServerSidebar.jsx`

---

## API Rules

- **Never** hardcode Railway URLs or `localhost` in components
- API base URL from `VITE_API_URL` env var
- All fetch calls in `api/offers.js` only — never inline in components

---

## Data Fetch Contract (`useOffers.js`)

1. On mount: immediate `fetchOffers()` with current filters
2. Every 10s: `fetchMeta()` → if `last_update` changed → **silent** re-fetch
3. Filter change: immediate re-fetch (not silent)
4. `enabledSources` toggle: client-side only, no API call

### Silent re-fetch pattern
- `silent=true` → do NOT set `loading=true`, do NOT clear `offers`
- Data "slides in" without UI flicker
- `setError(null)` on every fetch start regardless of silent flag

---

## State Rules

- `offers` — raw from API (all sources)
- `filteredOffers` — client-side filtered by `enabledSources`
- `enabledSources: Set<string> | null` — null = uninitialized (show all)
- `filters.server` = `display_server` group label: `(EU) Anniversary`
- `filters.server_name` = realm name: `Firemaw` (G2G only)

---

## OffersTable Rules

- Sort by `price_per_1k ASC` client-side
- `isExpensive`: G2G offer with `price_per_1k >= minPrice * 3` → show ⚠
- `positionValue > 9999` → render `∞` (not crash)
- `server_name` present → show realm; absent → show `currentServer` in grey
- Timestamps: convert UTC ISO to **local timezone** for display
- `isBest`: `price_per_1k === minPrice` → show ★ crown

---

## Sidebar Rules (`ServerSidebar.jsx`)

- Search: debounced 150ms, client-side only
- Query NOT cleared on realm/group selection (UX tested and reverted — keep)
- Servers sorted: Anniversary → SoD → Classic Era → Classic, then min_price ASC

---

## Error & Loading States

```jsx
if (error) return <ErrorState message={error} />
if (loading && offers.length === 0) return <LoadingState />
if (!loading && offers.length === 0) return <EmptyState />
// else render table
```

---

## Common Mistakes

| Mistake | Correct |
|---|---|
| `fetch('/api/offers')` | Use `VITE_API_URL` env var |
| Clear offers before re-fetch | Keep stale data, overwrite on success |
| `offers.filter(...)` in render | Use `filteredOffers` from hook |
| Format time as UTC | Convert to local timezone |
| Compute price in component | Use `price_per_1k` directly from API |

---

## What Frontend Must NOT Do

- Implement price calculation logic
- Redefine API response field names
- Make direct DB queries
- Use `localhost` in any URL
