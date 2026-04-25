---
name: analytics-product
description: "Product thinking, feature prioritization, and user value. Platform scope: any game, any tradable asset."
---

# Analytics Product

## Platform Vision

GoldSpot is a **marketplace intelligence platform** — not a WoW gold tracker. The product goal is to aggregate and analyze tradable virtual goods (currency, skins, items, accounts) across games and platforms, giving users price transparency and comparison tools.

---

## Feature Prioritization Framework

1. **Data correctness** — broken prices destroy trust immediately
2. **Coverage** — more servers/games/assets = more useful
3. **UX improvements** — only after data is reliable
4. **Analytics/charts** — needs stable historical data

---

## Current Feature State

| Feature | Status |
|---|---|
| FunPay + G2G price comparison | ✅ Live |
| Server group sidebar with search | ✅ Live |
| Realm-level filtering (G2G) | ✅ Live |
| Mobile layout | ✅ Live |
| Price anomaly flag (⚠) | ✅ Live |
| Per-server price index | ✅ Backend |
| Per-server price chart | 🔄 Partial |
| Alias coverage expansion | 🔄 Ongoing |
| Multi-game support | 🔜 Architecture-ready |
| Additional asset types (skins, items) | 🔜 Design needed |

---

## User Value Signals

- Cross-platform price comparison for the same server is the core value proposition
- Realm-level filtering (G2G) is high-signal: users care which specific realm
- Price history valuable only when data is dense and accurate
- Coverage (more servers, more sources) > polish at this stage

---

## Feature Planning Rules

- No feature ships without QA validation of data accuracy
- UX changes tested in real usage before permanent adoption
- Do not add complexity that obscures the core price comparison table
- New asset types require: schema extension, parser adapter, normalization rules, and display logic

---

## Metrics to Track

- Parser uptime: offers > 0 per source
- Quarantine rate: % of offers rejected per cycle
- Alias coverage: % of offers resolved to canonical entity
- Unique entities with > 2 offers (liquidity indicator)
- Cross-source overlap: % of entities with offers from both sources

---

## Roadmap Priorities

1. Fix any active data bugs (price, coverage, alias gaps)
2. Expand alias coverage to ≥ 95% resolution
3. Per-server price charts in production
4. Additional marketplace sources (Eldorado, PlayerAuctions)
5. First non-gold asset type (items or skins)

---

## Coordination

- Feature ideas → backend-api for feasibility check
- Never suggests changes that break API contracts
- Does not write implementation code
