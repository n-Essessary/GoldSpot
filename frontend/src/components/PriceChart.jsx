/**
 * PriceChart.jsx — TradingView lightweight-charts.
 * Серии: Index (area), Alliance ask и Horde ask (две линии).
 * Requires: npm install lightweight-charts
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, ColorType, LineStyle, CrosshairMode } from 'lightweight-charts'
import { API_BASE } from '../api/offers'
import styles from './PriceChart.module.css'

function smoothData(data, window = 3) {
  return data.map((point, i) => {
    const half = Math.floor(window / 2)
    const start = Math.max(0, i - half)
    const end   = Math.min(data.length - 1, i + half)
    const avg   = data.slice(start, end + 1).reduce((s, p) => s + p.value, 0) / (end - start + 1)
    return { ...point, value: avg }
  })
}

const PERIODS = [
  { label: '1H',  hours: 1,   points: 200 },
  { label: '6H',  hours: 6,   points: 300 },
  { label: '24H', hours: 24,  points: 400 },
  { label: '7D',  hours: 168, points: 500 },
  { label: '30D', hours: 720, points: 500 },
]

/** API values are per 1k gold; Per 1 divides by 1000.
 * @deprecated Prefer explicit `conv = v => applyPriceUnit(v, showPer1)` at call sites — clearer at scale. */
export const applyPriceUnit = (valuePer1k, showPer1) =>
  showPer1 ? valuePer1k / 1000 : valuePer1k

/** FiltersBar uses '' for «все»; backend expects `All`. */
export function normalizeFactionForApi(faction) {
  return faction || 'All'
}

/**
 * Parse region + version from a display_server group label.
 * "(EU) Anniversary" → { region: "EU", version: "Anniversary" }
 * "(EU) Season of Discovery" → { region: "EU", version: "Season of Discovery" }
 * Returns null if the format isn't recognised.
 */
export function _parseGroupLabel(serverSlug) {
  if (!serverSlug) return null
  const m = serverSlug.match(/^\(([A-Za-z]{2,})\)\s*(.+)$/)
  if (!m) return null
  return { region: m[1].toUpperCase(), version: m[2].trim() }
}

/**
 * Fetch live index + per-faction best ask from /price-index.
 * For faction=All: two requests (Alliance + Horde). Otherwise one request.
 * Returns per-faction fields; min_price is per-unit → ×1000 for per-1k display.
 */
export async function fetchLivePrice(serverName, region, version, faction) {
  const matchEntry = (data, fac) =>
    (data.entries ?? []).find(e =>
      e.server_name.toLowerCase() === serverName.toLowerCase() &&
      e.region.toUpperCase()      === region.toUpperCase() &&
      e.version.toLowerCase()     === version.toLowerCase() &&
      e.faction                   === fac
    )

  const fetchEntry = async fac => {
    try {
      const res = await fetch(`${API_BASE}/price-index?faction=${fac}`)
      if (!res.ok) return null
      const data = await res.json()
      return matchEntry(data, fac) ?? null
    } catch {
      return null
    }
  }

  try {
    if (faction === 'All' || faction === '' || !faction) {
      const [aEntry, hEntry] = await Promise.all([
        fetchEntry('Alliance'),
        fetchEntry('Horde'),
      ])
      if (!aEntry && !hEntry) return null
      const idx = aEntry?.index_price_per_1k ?? hEntry?.index_price_per_1k ?? null
      return {
        index_price_per_1k:       idx,
        best_ask_alliance_per_1k: aEntry != null ? aEntry.min_price * 1000 : null,
        best_ask_horde_per_1k:    hEntry != null ? hEntry.min_price * 1000 : null,
        alliance_sources:         aEntry?.sources ?? [],
        horde_sources:            hEntry?.sources ?? [],
      }
    }

    const entry = await fetchEntry(faction)
    if (!entry) return null
    return {
      index_price_per_1k: entry.index_price_per_1k,
      best_ask_alliance_per_1k: faction === 'Alliance' ? entry.min_price * 1000 : null,
      best_ask_horde_per_1k:    faction === 'Horde' ? entry.min_price * 1000 : null,
      alliance_sources:         faction === 'Alliance' ? (entry.sources ?? []) : [],
      horde_sources:            faction === 'Horde' ? (entry.sources ?? []) : [],
    }
  } catch {
    return null
  }
}

/**
 * PriceChart — TradingView lightweight-charts.
 *
 * Props:
 *   serverSlug    — display_server group label, e.g. "(EU) Anniversary" (required)
 *   refreshSignal — bumped on data updates to trigger re-fetch
 *   realmName     — specific realm, e.g. "Firemaw" (optional, Task 4)
 *                   When set → fetches per-server DB history
 *                   When unset → fetches legacy group OHLC from price_index_snapshots
 *   faction       — from FiltersBar ('' → treated as All)
 */
export function PriceChart({ serverSlug, refreshSignal, realmName, showPer1 = false, faction = 'All' }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef({})
  const fittedRef    = useRef(false)
  const isFirstLoadRef = useRef(true)
  const lastContextRef = useRef(null)
  const loadGenRef   = useRef(0)
  const loadGenContextKeyRef = useRef(null)
  const showPer1Ref = useRef(showPer1)
  const factionRef  = useRef(faction)
  const [period,  setPeriod]  = useState(PERIODS[2])   // 24H default
  const [loading, setLoading] = useState(false)
  const [empty,   setEmpty]   = useState(false)
  const [sources, setSources] = useState([])

  useEffect(() => { showPer1Ref.current = showPer1 }, [showPer1])
  useEffect(() => { factionRef.current = faction }, [faction])

  // ── Инициализация графика (один раз) ───────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.offsetWidth,
      layout: {
        background:  { type: ColorType.Solid, color: 'transparent' },
        textColor:   'rgba(156,154,146,0.8)',
        fontFamily:  'var(--font-mono, monospace)',
        fontSize:    11,
      },
      grid: {
        vertLines: { color: 'rgba(156,154,146,0.06)' },
        horzLines: { color: 'rgba(156,154,146,0.06)' },
      },
      crosshair: {
        mode:     CrosshairMode.Normal,
        vertLine: { color: 'rgba(156,154,146,0.4)', style: LineStyle.Dashed, labelVisible: true },
        horzLine: { color: 'rgba(156,154,146,0.4)', style: LineStyle.Dashed, labelVisible: true },
      },
      localization: {
        timeFormatter: (utcTimestamp) => {
          const d = new Date(utcTimestamp * 1000)
          const pad = n => String(n).padStart(2, '0')
          return `${pad(d.getDate())}.${pad(d.getMonth()+1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
        },
      },
      rightPriceScale: {
        borderColor:   'rgba(156,154,146,0.15)',
        scaleMargins:  { top: 0.05, bottom: 0.05 },
        minimumWidth:  60,
      },
      timeScale: {
        borderColor:    'rgba(156,154,146,0.15)',
        timeVisible:    true,
        secondsVisible: false,
        fixLeftEdge:    true,
        fixRightEdge:   true,
        rightOffset:    0,
        minBarSpacing:  0.5,
        barSpacing:     6,
        lockVisibleTimeRangeOnResize: true,
      },
    })

    chart.timeScale().applyOptions({ rightOffset: 0 })

    // index_price — основная зелёная area
    seriesRef.current.index = chart.addAreaSeries({
      lineColor:              '#1D9E75',
      topColor:               'rgba(30,158,117,0.18)',
      bottomColor:            'rgba(30,158,117,0.0)',
      lineWidth:              2,
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat:            {
        type:      'custom',
        formatter: p => `$${Number(p).toFixed(2)}`,
        minMove:   0.01,
      },
      title:                  '',
    })

    // best_ask — тонкая жёлтая точечная
    seriesRef.current.ask = chart.addLineSeries({
      color:                  'rgba(186,117,23,0.85)',
      lineWidth:              1,
      lineStyle:              LineStyle.SparseDotted,
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat:            {
        type:      'custom',
        formatter: p => `$${Number(p).toFixed(2)}`,
        minMove:   0.01,
      },
      title:                  '',
    })

    seriesRef.current.askAlliance = chart.addAreaSeries({
      lineColor:              'rgba(74,144,217,0.75)',
      topColor:               'rgba(74,144,217,0.10)',
      bottomColor:            'rgba(74,144,217,0.0)',
      lineWidth:              1,
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat: {
        type:      'custom',
        formatter: p => `$${Number(p).toFixed(2)}`,
        minMove:   0.01,
      },
      title: '',
    })

    seriesRef.current.askHorde = chart.addAreaSeries({
      lineColor:              'rgba(192,57,43,0.75)',
      topColor:               'rgba(192,57,43,0.10)',
      bottomColor:            'rgba(192,57,43,0.0)',
      lineWidth:              1,
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat: {
        type:      'custom',
        formatter: p => `$${Number(p).toFixed(2)}`,
        minMove:   0.01,
      },
      title: '',
    })

    // Floating crosshair tooltip — follows cursor
    const tooltip = document.createElement('div')
    tooltip.style.cssText = `
      position: absolute;
      pointer-events: none;
      display: none;
      z-index: 10;
      background: rgba(14,16,22,0.92);
      border: 1px solid rgba(156,154,146,0.2);
      border-radius: 5px;
      padding: 6px 8px;
      display: none;
      flex-direction: column;
      gap: 5px;
      white-space: nowrap;
    `
    containerRef.current.style.position = 'relative'
    containerRef.current.appendChild(tooltip)

    chart.subscribeCrosshairMove(param => {
      if (
        !param.time ||
        !param.point ||
        param.point.x < 0 ||
        param.point.y < 0
      ) {
        tooltip.style.display = 'none'
        return
      }

      const indexData = param.seriesData.get(seriesRef.current.index)
      const faction = factionRef.current
      const isAll      = faction === '' || faction === 'All'
      const isAlliance = faction === 'Alliance'
      const isHorde    = faction === 'Horde'

      const askData      = param.seriesData.get(seriesRef.current.ask)
      const allianceData = param.seriesData.get(seriesRef.current.askAlliance)
      const hordeData    = param.seriesData.get(seriesRef.current.askHorde)

      const chipOk = d =>
        d != null &&
        d.value != null &&
        !Number.isNaN(Number(d.value)) &&
        Number(d.value) !== 0

      if (!indexData && !chipOk(askData) && !chipOk(allianceData) && !chipOk(hordeData)) {
        tooltip.style.display = 'none'
        return
      }

      const fmt = v => showPer1Ref.current
        ? `$${Number(v).toFixed(5)}`
        : `$${Number(v).toFixed(2)}`

      const rows = []
      if (indexData) rows.push({
        label: 'Market Price',
        color: '#1D9E75',
        value: fmt(indexData.value),
      })

      if (isAll) {
        if (allianceData) rows.push({ label: 'Cheapest Alliance', color: '#4A90D9', value: fmt(allianceData.value) })
        if (hordeData)    rows.push({ label: 'Cheapest Horde',    color: '#C0392B', value: fmt(hordeData.value) })
      } else if (isAlliance) {
        if (allianceData) rows.push({ label: 'Cheapest Alliance', color: '#4A90D9', value: fmt(allianceData.value) })
      } else if (isHorde) {
        if (hordeData) rows.push({ label: 'Cheapest Horde', color: '#C0392B', value: fmt(hordeData.value) })
      } else {
        if (askData) rows.push({ label: 'Cheapest', color: '#9A6010', value: fmt(askData.value) })
      }

      tooltip.innerHTML = rows.map(r => `
        <div style="display:flex; align-items:center; gap:6px; line-height:1;">
          <span style="
            background: ${r.color};
            color: #fff;
            font-family: var(--font-mono, monospace);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.04em;
            padding: 2px 6px;
            border-radius: 3px;
            white-space: nowrap;
          ">${r.label}</span>
          <span style="
            background: ${r.color};
            color: #fff;
            font-family: var(--font-mono, monospace);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.04em;
            padding: 2px 6px;
            border-radius: 3px;
          ">${r.value}</span>
        </div>
      `).join('')
      tooltip.style.display = rows.length > 0 ? 'flex' : 'none'
      const tooltipWidth  = tooltip.offsetWidth
      const tooltipHeight = tooltip.offsetHeight
      const chartHeight   = containerRef.current.offsetHeight

      // Float LEFT of crosshair, flip RIGHT if near left edge
      let left = param.point.x - tooltipWidth - 12
      if (left < 4) left = param.point.x + 12

      let top = param.point.y - tooltipHeight / 2
      if (top < 4) top = 4
      if (top + tooltipHeight > chartHeight - 4) top = chartHeight - tooltipHeight - 4

      tooltip.style.left = `${left}px`
      tooltip.style.top  = `${top}px`
    })

    chartRef.current = chart

    // Адаптивная ширина
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        if (chartRef.current)
          chartRef.current.applyOptions({ width: e.contentRect.width })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current  = null
      seriesRef.current = {}
      tooltip.remove()
    }
  }, [])

  useEffect(() => {
    chartRef.current?.applyOptions({
      localization: {
        priceFormatter: p => (showPer1 ? `$${p.toFixed(5)}` : `$${p.toFixed(2)}`),
      },
    })
  }, [showPer1])

  useEffect(() => {
    const fmt2 = p => showPer1
      ? `$${Number(p).toFixed(5)}`
      : `$${Number(p).toFixed(2)}`

    seriesRef.current.index?.applyOptions?.({
      title:                  '',
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat: {
        type:      'custom',
        formatter: p => fmt2(p),
        minMove:   showPer1 ? 0.00001 : 0.01,
      },
    })
    seriesRef.current.ask?.applyOptions?.({
      title:                  '',
      crosshairMarkerVisible: true,
      lastPriceAnimation:     0,
      priceLineVisible:       false,
      lastValueVisible:       true,
      priceFormat: {
        type:      'custom',
        formatter: p => fmt2(p),
        minMove:   showPer1 ? 0.00001 : 0.01,
      },
    })
    seriesRef.current.askAlliance?.applyOptions?.({
      priceFormat: {
        type:      'custom',
        formatter: p => fmt2(p),
        minMove:   showPer1 ? 0.00001 : 0.01,
      },
    })
    seriesRef.current.askHorde?.applyOptions?.({
      priceFormat: {
        type:      'custom',
        formatter: p => fmt2(p),
        minMove:   showPer1 ? 0.00001 : 0.01,
      },
    })
  }, [showPer1])

  useEffect(() => {
    isFirstLoadRef.current = false
  }, [])

  // ── Загрузка данных ────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!serverSlug || serverSlug === 'all') return
    const contextKey = `${serverSlug}|${realmName}|${faction}|${period.label}|${showPer1}`
    if (loadGenContextKeyRef.current !== contextKey) {
      loadGenRef.current += 1
      loadGenContextKeyRef.current = contextKey
    }
    const gen = loadGenRef.current
    setLoading(true)
    try {
      const factionApi = normalizeFactionForApi(faction)
      let points = []
      let allianceAskPoints = []
      let hordeAskPoints = []

      const toTS = p => {
        const raw = p.time ?? p.recorded_at
        if (typeof raw === 'number') return raw
        return Math.floor(new Date(raw).getTime() / 1000)
      }

      const parsed = _parseGroupLabel(serverSlug)
      const allRealmMode = Boolean(realmName && parsed && factionApi === 'All')

      if (realmName && parsed) {
        // ── Task 4 mode: per-server history from DB ───────────────────────────
        if (factionApi === 'All') {
          const mkParams = fac => new URLSearchParams({
            server:  realmName,
            region:  parsed.region,
            version: parsed.version,
            faction: fac,
            hours:   String(period.hours),
            last:    String(period.points),
          })
          const [resA, resH] = await Promise.all([
            fetch(`${API_BASE}/price-history?${mkParams('Alliance')}`),
            fetch(`${API_BASE}/price-history?${mkParams('Horde')}`),
          ])
          const [dataA, dataH] = await Promise.all([
            resA.ok ? resA.json() : Promise.resolve({ points: [] }),
            resH.ok ? resH.json() : Promise.resolve({ points: [] }),
          ])
          const rawA = dataA.points ?? []
          const rawH = dataH.points ?? []

          const rawIndex = rawA.length >= rawH.length ? rawA : rawH
          if (rawIndex.length > 0) {
            points = rawIndex.map(p => ({
              time:      toTS(p),
              avg_price: p.index_price_per_1k,
              best_ask:  null,
              sources:   [],
            }))
          }
          allianceAskPoints = rawA
            .map(p => ({ time: toTS(p), value: p.best_ask, sources: p.sources ?? [] }))
            .filter(p => (p.value || 0) > 0)
          hordeAskPoints = rawH
            .map(p => ({ time: toTS(p), value: p.best_ask, sources: p.sources ?? [] }))
            .filter(p => (p.value || 0) > 0)
        } else {
          // GET /price-history?server={realm}&region={EU}&version={Anniversary}&faction={f}
          const params = new URLSearchParams({
            server:  realmName,
            region:  parsed.region,
            version: parsed.version,
            faction: factionApi,
            hours:   String(period.hours),
            last:    String(period.points),
          })
          const res = await fetch(`${API_BASE}/price-history?${params}`)
          if (res.ok) {
            const data = await res.json()
            // per-server endpoint returns ServerHistoryResponse with points[]
            const raw = data.points ?? []
            if (raw.length > 0) {
              points = raw.map(p => ({
                time:      toTS(p),
                avg_price: p.index_price_per_1k,
                best_ask:  p.best_ask ?? p.index_price_per_1k,
                sources:   [],
              }))
            }
          }
        }
        // Group-level view may fall through to legacy OHLC below; realm mode does not.
      }

      if (loadGenRef.current !== gen) return

      if (points.length === 0 && !(realmName && parsed)) {
        // ── Legacy mode (group OHLC) — only for group-level view, never for realm ──
        const params = new URLSearchParams({
          server:     serverSlug,
          faction: factionApi,
          last_hours: String(period.hours),
          max_points: String(period.points),
        })
        const res = await fetch(`${API_BASE}/price-history/ohlc?${params}`)
        if (!res.ok) {
          setLoading(false)
          return
        }
        const data = await res.json()
        const rawPts = data.points ?? []
        points = rawPts.map(p => {
          const raw = p.best_ask ?? p.close ?? 0
          return {
            ...p,
            time:              toTS(p),
            avg_price:         p.avg_price ?? p.index_price_per_1k ?? p.close ?? 0,
            best_ask:          raw,
            best_ask_alliance: raw,
            best_ask_horde:    raw,
            alliance_sources:  [],
            horde_sources:     [],
            sources:           p.sources ?? [],
          }
        })
      }

      if (loadGenRef.current !== gen) return

      // Append live point — always shows current price on right edge
      if (realmName && parsed) {
        const live = await fetchLivePrice(realmName, parsed.region, parsed.version, factionApi)
        if (loadGenRef.current !== gen) return
        if (live) {
          const nowTs = Math.floor(Date.now() / 1000)
          const lastTs = points.length > 0
            ? toTS(points[points.length - 1])
            : 0
          const hasAsk = live.best_ask_alliance_per_1k != null
            || live.best_ask_horde_per_1k != null
          if (nowTs > lastTs && (live.index_price_per_1k != null || hasAsk)) {
            const asrc = live.alliance_sources ?? []
            const hsrc = live.horde_sources ?? []
            const liveBest = factionApi === 'Alliance'
              ? live.best_ask_alliance_per_1k
              : factionApi === 'Horde'
                ? live.best_ask_horde_per_1k
                : null
            points = [
              ...points,
              {
                recorded_at:         new Date().toISOString(),
                index_price_per_1k:  live.index_price_per_1k,
                avg_price:           live.index_price_per_1k,
                best_ask:            liveBest,
                best_ask_alliance:   live.best_ask_alliance_per_1k,
                best_ask_horde:      live.best_ask_horde_per_1k,
                alliance_sources:    asrc,
                horde_sources:       hsrc,
                sources:             [...new Set([...asrc, ...hsrc])],
              },
            ]
            if (allRealmMode) {
              if (live.best_ask_alliance_per_1k != null) {
                allianceAskPoints = [
                  ...allianceAskPoints,
                  { time: nowTs, value: live.best_ask_alliance_per_1k, sources: asrc },
                ]
              }
              if (live.best_ask_horde_per_1k != null) {
                hordeAskPoints = [
                  ...hordeAskPoints,
                  { time: nowTs, value: live.best_ask_horde_per_1k, sources: hsrc },
                ]
              }
            }
          }
        }
      }

      setEmpty(points.length === 0)
      if (points.length === 0) {
        setLoading(false)
        return
      }

      const conv = v => applyPriceUnit(v, showPer1)

      const contextChanged = lastContextRef.current !== contextKey
      const savedRange = (!contextChanged && fittedRef.current)
        ? chartRef.current?.timeScale()?.getVisibleLogicalRange()
        : null

      const indexData = points.map(p => ({
        time:  toTS(p),
        value: conv(p.avg_price || p.close || 0),
      }))
      const lastIndex = indexData[indexData.length - 1]

      // Pin last point to now — prevents right-side gap caused by
      // time scale stretching to current time beyond last data point.
      const nowTs = Math.floor(Date.now() / 1000)
      const extendToNow = data => {
        if (data.length === 0) return data
        const last = data[data.length - 1]
        return nowTs > last.time
          ? [...data, { time: nowTs, value: last.value }]
          : data
      }
      const askDataRaw = points
        .filter(p => (p.best_ask || 0) > 0)
        .map(p => ({ time: toTS(p), value: conv(p.best_ask) }))
      const askData = extendToNow(askDataRaw)
      const allianceData = extendToNow(
        allianceAskPoints
          .filter(p => (p.value || 0) > 0)
          .map(p => ({ time: p.time, value: conv(p.value) }))
      )
      const hordeData = extendToNow(
        hordeAskPoints
          .filter(p => (p.value || 0) > 0)
          .map(p => ({ time: p.time, value: conv(p.value) }))
      )

      seriesRef.current.index?.setData(smoothData(indexData))
      if (allRealmMode) {
        seriesRef.current.ask?.applyOptions({ visible: false })
        seriesRef.current.askAlliance?.applyOptions({ visible: true })
        seriesRef.current.askHorde?.applyOptions({ visible: true })
        seriesRef.current.askAlliance?.setData(smoothData(allianceData))
        seriesRef.current.askHorde?.setData(smoothData(hordeData))
      } else {
        // Hide yellow ask always — use colored faction series
        seriesRef.current.ask?.applyOptions({ visible: false })

        const extendToNow = (data) => {
          if (data.length === 0) return data
          const last = data[data.length - 1]
          return nowTs > last.time ? [...data, { time: nowTs, value: last.value }] : data
        }

        if (factionApi === 'Alliance') {
          seriesRef.current.askAlliance?.applyOptions({ visible: true })
          seriesRef.current.askHorde?.applyOptions({ visible: false })
          seriesRef.current.askHorde?.setData([])
          seriesRef.current.askAlliance?.setData(smoothData(extendToNow(askData)))
        } else if (factionApi === 'Horde') {
          seriesRef.current.askHorde?.applyOptions({ visible: true })
          seriesRef.current.askAlliance?.applyOptions({ visible: false })
          seriesRef.current.askAlliance?.setData([])
          seriesRef.current.askHorde?.setData(smoothData(extendToNow(askData)))
        } else {
          // fallback — hide all ask series
          seriesRef.current.ask?.applyOptions({ visible: false })
          seriesRef.current.askAlliance?.applyOptions({ visible: false })
          seriesRef.current.askHorde?.applyOptions({ visible: false })
        }
      }

      const latestIndex = indexData[indexData.length - 1]
      if (latestIndex && nowTs > latestIndex.time) {
        seriesRef.current.index?.update({ time: nowTs, value: latestIndex.value })
      }

      const allSrc = new Set(
        [
          ...points.flatMap(p => p.sources || []),
          ...allianceAskPoints.flatMap(p => p.sources || []),
          ...hordeAskPoints.flatMap(p => p.sources || []),
        ]
      )
      setSources([...allSrc])

      const timeScale = chartRef.current?.timeScale()

      // Apply default Y-axis padding — centers price with ~5% breathing room.
      // This is a display default, not a zoom constraint; user can still zoom freely.
      if (!fittedRef.current || contextChanged) {
        chartRef.current?.priceScale('right').applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.05, bottom: 0.05 },
        })
        timeScale?.fitContent()
        fittedRef.current = true
      } else if (savedRange) {
        timeScale?.setVisibleLogicalRange(savedRange)
      } else {
        chartRef.current?.priceScale('right').applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.05, bottom: 0.05 },
        })
        timeScale?.fitContent()
      }

      lastContextRef.current = contextKey
    } catch {
      // сетевой сбой — граф остаётся со старыми данными, loading скрывается
    } finally {
      setLoading(false)
    }
  }, [serverSlug, realmName, faction, period, showPer1])

  useEffect(() => { loadData() }, [loadData, refreshSignal])

  return (
    <div className={styles.wrapper}>
      <div className={styles.controls}>
        <div className={styles.group}>
          {PERIODS.map(p => (
            <button
              key={p.label}
              className={period.label === p.label ? styles.active : styles.btn}
              onClick={() => setPeriod(p)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {sources.length > 0 && (
          <span className={styles.sources}>{sources.join(' + ')}</span>
        )}
        {loading && <span className={styles.hint}>…</span>}
      </div>

      <div
        ref={containerRef}
        className={styles.chart}
        style={{ height: 240 }}
      />

      {empty && !loading && (
        <div className={styles.empty}>Нет данных за выбранный период</div>
      )}
    </div>
  )
}
