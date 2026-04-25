---
name: layout-ui
description: "UI layout, visual hierarchy, responsive design, and component structure."
---

# Layout UI

## Layout Structure

```
App
├── ServerSidebar (left, collapsible on mobile)
│   ├── Search input (debounced 150ms, client-side)
│   └── Server groups (sorted by version, then min_price)
├── Main content
│   ├── StatsBar (spread %, source counts, last update)
│   ├── FiltersBar (faction toggle, source toggle, sort)
│   └── OffersTable
```

---

## OffersTable Column Order

`# | Platform | Server · Faction | Price/1K | Position Value | Volume | Seller | Updated | Buy`

- `#` rank: ★ for best price, number otherwise
- Platform: colored badge (FunPay=#22c55e, G2G=#a78bfa)
- Faction: colored text (Horde=#ff4d6a, Alliance=#4d9fff)
- Price/1K: primary metric — highest visual weight
- Position Value: `price_per_1k × (amount_gold / 1000)` → `∞` if > $9999
- Buy button: primary CTA, gold highlight on best price row

---

## Color System

```css
--src-color     /* per-source accent, set via inline style */
--text-secondary /* grey for fallback server labels */
```

Faction and source colors are constants in `OffersTable.jsx` — do NOT derive from theme.

---

## Top-3 Price Highlighting

- `isBest` (rank 1): gold background + ★ crown + gold buy button
- `isTop3` (ranks 2–3): subtle highlight
- `isExpensive` (G2G ≥ 3× min): ⚠ prefix before price
- `posHuge` (position value > $9999): muted style + `∞`

---

## Mobile Rules

- Sidebar hidden by default → burger menu toggle
- Table: horizontal scroll on narrow screens (do not hide columns)
- Touch targets: min 44px height
- Burger menu: overlay sidebar, close on outside click or server selection

---

## Rules

- `mono` class for all numeric data (price, volume, time)
- Never show empty table — always show loading, error, or empty state
- `currentServer` prop on OffersTable provides group context for G2G fallback label
- Search input: never clear on selection
