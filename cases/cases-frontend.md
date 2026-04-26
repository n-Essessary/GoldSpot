# Case Log — Frontend

React + Vite + lightweight-charts. Mostly chart and data-fetching quirks.

---

## [BUG] PriceChart timezone offset wrong

**Severity:** medium (chart hours displayed in wrong timezone)

### Symptom
Time axis on PriceChart showed UTC hours instead of user's local time. Tooltip times also wrong.

### Root cause
Used `userOffsetSec` calculation that didn't account for DST or user's actual locale.

### Resolution
Use `localization.timeFormatter` with native `Date`:
```js
localization: {
  timeFormatter: ts => new Date(ts * 1000).getHours() + ':00'
}
```
Browser handles timezone, DST, and locale correctly.

### Prevention
Avoid manual offset math for time display. Native `Date` already does this right.

---

## [BUG] `/price-history` endpoint returned wrong order

**Severity:** medium (chart rendered backwards)

### Root cause
SQL query used `ORDER BY ASC` but lightweight-charts expects descending raw → ascending after reverse for performance reasons in this app's pattern.

### Resolution
Changed to `ORDER BY DESC` + `.reverse()` in Python before returning.

### Notes
This is app-specific. The fix matched chart's rendering expectation, not a general SQL convention.

---

## [BUG] Chart zoom reset on every background refresh

**Severity:** medium (UX friction — user lost zoom every 30s)

### Symptom
User zooms into a region of price history. 30s later, parser cycle finishes, `setData` called, zoom resets to fit.

### Root cause
`setData` resets visible range by default.

### Resolution
- Save `getVisibleLogicalRange()` BEFORE `setData`
- Restore after `setData`
- Use `fittedRef` flag to distinguish first load (should fit) from subsequent updates (should preserve)

```js
const range = chart.timeScale().getVisibleLogicalRange()
series.setData(newData)
if (fittedRef.current && range) {
  chart.timeScale().setVisibleLogicalRange(range)
}
fittedRef.current = true
```

### Prevention
Apply same save/restore pattern to any chart receiving live updates.

---

## [BUG] Right-edge gap on PriceChart

**Severity:** low (visual quirk)

### Symptom
Empty space between rightmost data point and chart's right edge.

### Failed approaches
- Hardcoding `barSpacing/2` offset → fragile, broke on any zoom level

### Resolution
Pinned now-point — synthetic data point at current timestamp ensures rightmost edge stays anchored.

### Prevention
Rule in chat instructions: "right-edge gap → use pinned now-point — never hardcode `barSpacing/2`"

---

## [ARCH] PriceChart faction split

**Status:** production
**Files:** `frontend/src/components/PriceChart.jsx`

### Behavior
- `faction=All` → two parallel `/price-history` requests (Alliance + Horde) → three series:
  - `index` (green area) — combined cheapest
  - `askAlliance` (blue area) — Alliance cheapest
  - `askHorde` (red area) — Horde cheapest
  - Yellow ask series always hidden
- `faction=Alliance/Horde` → single colored series

### Tooltip
- Chips sorted by descending price (matches visual line order top-to-bottom)
- Smoothing via `smoothData(window=3)` applied before `setData`
- Labels: "Cheapest Alliance" / "Cheapest Horde"

### Why parallel fetch instead of single endpoint with both factions
Endpoint contract was already faction-scoped. Adding a "both" mode would require API contract change. Two parallel requests + client-side merge is simpler and doesn't break existing consumers.

---

## [ARCH] `useOffers` hook: meta-poll pattern

**Status:** production
**Files:** `frontend/src/hooks/useOffers.js`

### Pattern
```
every 10s:
  GET /meta → { last_update }
  if last_update changed:
    GET /offers → update state
  else:
    do nothing
```

### Why not poll `/offers` directly
- `/offers` is heavy (full list), `/meta` is tiny
- Reduces backend load 10×
- Reduces frontend re-renders to actual data changes only

### Critical rule
Never clear state before update arrives — causes flicker. Update is replace-on-success only.

---

## [ARCH] Source filter is client-side only

**Status:** production rule

### Rule
- `enabledSources` filter (FunPay/G2G toggle) → applied in frontend after fetch
- Server filter, faction filter, version filter → sent to backend as query params

### Why
Source filter changes are instant UX. Round-tripping to backend for "hide G2G" creates 200ms delay for no reason. Server/faction filters change the dataset structure → backend handles.

---

## [ARCH] ServerSidebar: multi-source indicator

**Status:** production
**Files:** `frontend/src/components/ServerSidebar.jsx`

### Behavior
Green dot (5px, `#1D9E75`) on realm row when realm has offers from >1 source.

### Backend contract
`/servers` returns `realm_sources: dict[str, list[str]]` in `ServerGroup`. Frontend reads `sources.length > 1` to decide indicator.

### Implementation note
`VERSION_ORDER` and `openVersions` default updated for Retail support:
```js
const order = ['Retail', 'MoP Classic', 'Classic Era']
```

---

## [ARCH] lightweight-charts: axis badges

**Status:** stable working pattern

### Pattern
```js
{
  lastValueVisible: true,
  title: 'Cheapest Alliance',
  priceFormat: { ... }
}
+ chart.timeScale({ rightOffset: 0 })
```

### Why this combo
Native line-embedded badges in lightweight-charts are unstable across versions. The `lastValueVisible: true` + `title` + `rightOffset: 0` combo reliably produces axis badges that don't disappear or jitter.

---

## [INFRA] Browser MCP inspection pattern (frontend debugging)

**Pattern**
```js
fetch(url).then(r => r.json()).then(d => { window._var = d })
// then in next call:
window._var
```

### Use direct Railway URL, not Vercel proxy
Vercel proxy adds caching layer — debugging stale-cache issues becomes impossible if you can't bypass.

### When to use
- Verifying API response shape matches frontend assumption
- Debugging why a specific server doesn't appear in UI
- Comparing live API response vs cached frontend state

---

## [BUG] Price unit toggle placed in table header instead of FiltersBar

**Severity:** low (wrong placement, UX issue)
**Files:** `frontend/src/components/OffersTable.jsx`, `frontend/src/components/FiltersBar.jsx`

### Symptom
Toggle appeared inside `<th>` of the offers table. Classic Era showed no toggle at all.

### Root cause
Cursor placed the toggle component in `OffersTable.jsx` thead rather than `FiltersBar.jsx`.
Visibility condition only checked for Retail/MoP → Classic Era had no toggle despite needing /1 | /1K.

### Resolution
- Toggle moved to `FiltersBar.jsx` inside `._bar` div, next to faction selector
- Three-state `priceUnit`: `"per_unit"` | `"per_1k"` | `"per_1m"`
- Version logic:
  - Retail / MoP Classic → show `/1K` | `/1M`
  - Classic Era / Anniversary / Seasonal / (no filter) → show `/1` | `/1K`
- Display conversion: `per_unit = price_per_1k / 1000`, `per_1k = as-is`, `per_1m = price_per_1k * 1000`

### Prevention
- Toggle placement rule: version/filter controls belong in FiltersBar, never in table structure
- New version added → explicitly define its toggle options in the version→toggle map

## [BUG] PriceChart freezes 2–10s on fast realm/period switching

**Severity:** high (UX — chart unresponsive under rapid interaction)

### Symptom
Switching realms or periods quickly caused chart to freeze for 2–10 seconds. Worse with faction=All
(6 requests per loadData call). Reproduced consistently on 30D period.

### Root cause
1. No AbortController — old fetches not cancelled on new user action. Browser HTTP/1.1 limit
   (6 connections/origin) queued new requests behind in-flight old ones.
2. Background refreshSignal triggered `setLoading(true)` → spinner + chart flicker every 10–30s.

### Resolution
- Added `abortRef = useRef(null)` at component level
- Top of `loadData`: `abortRef.current?.abort()` + new `AbortController`, signal passed to all fetches
- AbortError caught silently — no error state set
- `loadData(silent=false)` signature added: user actions pass `false` (show spinner), refreshSignal
  effect passes `true` (no spinner, data slides in)

### Prevention
- Rule: any chart receiving live updates must have AbortController on its fetch function
- Rule: distinguish user-action load (show spinner) from background refresh (silent) at call site

---

## [BUG] priceUnit не передавался в PriceChart — toggle не влиял на график

**Severity:** high (toggle /1K↔/1M не работал для всех версий)
**Files:** `frontend/src/App.jsx`, `frontend/src/components/PriceChart.jsx`

### Symptom
Переключение /1 | /1K | /1M в FiltersBar не меняло отображение цен на графике.
Для Retail/MoP: после перехода на /1M график оставался в /1K масштабе.

### Root cause
В `App.jsx` было захардкожено `showPer1={false}` вместо передачи `priceUnit` из стейта.
`PriceChart` принимал только `showPer1: boolean` — без поддержки третьего состояния `per_1m`.
`applyPriceUnit` умел только два состояния: `per_1k` (default) и `per_unit`.

### Resolution
- `showPer1: boolean` → `priceUnit: string` (`per_unit` | `per_1k` | `per_1m`) во всём PriceChart
- `applyPriceUnit` расширен: `per_unit → /1000`, `per_1m → *1000`, `per_1k → as-is`
- App.jsx: `priceUnit={priceUnit}` вместо `showPer1={false}`
- Все внутренние refs, deps, форматтеры переименованы с `showPer1` на `priceUnit`

### Prevention
- При добавлении нового состояния toggle — всегда проверять что проп долетает до PriceChart
- `showPer1: boolean` — антипаттерн для N>2 состояний; использовать строковый enum

---

## [BUG] Спиннер показывался при каждом переключении сервера

**Severity:** low (UX friction)
**Files:** `frontend/src/components/PriceChart.jsx`

### Symptom
При клике на другой сервер в ServerSidebar график мигал спиннером (~300ms).

### Root cause
Один `useEffect` с `loadData(false)` срабатывал на все deps включая `serverSlug`/`realmName`.
`silent=false` всегда показывает спиннер.

### Resolution
Разделить на два useEffect:
```js
// period/faction/unit — показывать спиннер (пользователь ожидает новые данные)
useEffect(() => { loadData(false) }, [period, faction, priceUnit])
// server/realm — silent (график остаётся видимым во время загрузки)
useEffect(() => { loadData(true) }, [serverSlug, realmName])
```

### Prevention
Silent load при навигации между сущностями; спиннер только при смене фильтров.

---

## [BUG] Цены Retail/MoP округлялись неправильно ($0.0499 → $0.05)

**Severity:** medium (потеря точности для дешёвых активов)
**Files:** `frontend/src/components/PriceChart.jsx`

### Root cause
Все форматтеры использовали `toFixed(2)` для `per_1k` режима.
Retail/MoP цены ~$0.03–$0.15 — два знака недостаточно.

### Resolution
Добавлен умный форматтер `fmtPrice`:
```js
const fmtPrice = v => {
  const n = Number(v)
  if (n < 0.10) return `$${n.toFixed(4)}`
  if (n < 1.00) return `$${n.toFixed(3)}`
  return `$${n.toFixed(2)}`
}
```
Применён во всех форматтерах (tooltip, axis, localization).

### Prevention
При добавлении новой игры/версии с ценами <$0.10 — убедиться что `fmtPrice` покрывает диапазон.