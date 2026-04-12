/**
 * PriceChart.jsx — TradingView lightweight-charts.
 * Две серии: Index (area) и Best ask (линия).
 * Requires: npm install lightweight-charts
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, ColorType, LineStyle, CrosshairMode } from 'lightweight-charts'
import { API_BASE } from '../api/offers'
import styles from './PriceChart.module.css'

const PERIODS = [
  { label: '1H',  hours: 1,   points: 200 },
  { label: '6H',  hours: 6,   points: 300 },
  { label: '24H', hours: 24,  points: 400 },
  { label: '7D',  hours: 168, points: 500 },
  { label: '30D', hours: 720, points: 500 },
]

/** API values are per 1k gold; Per 1 divides by 1000. */
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
 * Fetch current live index+best_ask for a specific server from /price-index.
 * Returns { index_price_per_1k, best_ask_per_1k } or null on failure.
 */
export async function fetchLivePrice(serverName, region, version, faction) {
  try {
    const res = await fetch(`${API_BASE}/price-index?faction=${faction}`)
    if (!res.ok) return null
    const data = await res.json()
    const entry = (data.entries ?? []).find(e =>
      e.server_name.toLowerCase() === serverName.toLowerCase() &&
      e.region.toUpperCase()      === region.toUpperCase() &&
      e.version.toLowerCase()     === version.toLowerCase() &&
      e.faction                   === faction
    )
    if (!entry) return null
    return {
      index_price_per_1k: entry.index_price_per_1k,
      best_ask_per_1k:    entry.min_price * 1000,  // min_price is per-unit
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
  const [period,  setPeriod]  = useState(PERIODS[2])   // 24H default
  const [loading, setLoading] = useState(false)
  const [empty,   setEmpty]   = useState(false)
  const [sources, setSources] = useState([])

  // ── Инициализация графика (один раз) ───────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
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
      rightPriceScale: {
        borderColor:  'rgba(156,154,146,0.15)',
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor:    'rgba(156,154,146,0.15)',
        timeVisible:    true,
        secondsVisible: false,
        fixLeftEdge:    true,
        fixRightEdge:   true,
      },
    })

    // index_price — основная зелёная area
    seriesRef.current.index = chart.addAreaSeries({
      lineColor:              '#1D9E75',
      topColor:               'rgba(30,158,117,0.18)',
      bottomColor:            'rgba(30,158,117,0.0)',
      lineWidth:              2,
      crosshairMarkerVisible: true,
      priceLineVisible:       true,
      priceLineColor:         'rgba(30,158,117,0.6)',
      lastValueVisible:       true,
      priceFormat:            {
        type:      'price',
        precision: 2,
        minMove:   0.01,
      },
      title:                  'Index',
    })

    // best_ask — тонкая жёлтая точечная
    seriesRef.current.ask = chart.addLineSeries({
      color:                  'rgba(186,117,23,0.85)',
      lineWidth:              1,
      lineStyle:              LineStyle.SparseDotted,
      crosshairMarkerVisible: false,
      priceLineVisible:       false,
      lastValueVisible:       true,
      title:                  'Best ask',
    })

    // Floating crosshair tooltip — follows cursor
    const tooltip = document.createElement('div')
    tooltip.style.cssText = `
      position: absolute;
      pointer-events: none;
      font-family: var(--font-mono, monospace);
      font-size: 11px;
      line-height: 1.5;
      color: rgba(220,220,220,0.95);
      background: rgba(14,14,20,0.92);
      border: 1px solid rgba(156,154,146,0.2);
      border-radius: 3px;
      padding: 3px 7px;
      white-space: nowrap;
      display: none;
      z-index: 10;
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
      const askData   = param.seriesData.get(seriesRef.current.ask)

      if (!indexData && !askData) {
        tooltip.style.display = 'none'
        return
      }

      const fmt = v => v != null ? `$${Number(v).toFixed(2)}` : '—'

      tooltip.innerHTML = [
        indexData ? `<span style="color:#1D9E75">▸ Index&nbsp;&nbsp;&nbsp;${fmt(indexData.value)}</span>` : '',
        askData   ? `<span style="color:#BA7517">▸ Best ask ${fmt(askData.value)}</span>`                 : '',
      ].filter(Boolean).join('<br/>')

      tooltip.style.display = 'block'
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

  // ── Загрузка данных ────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!serverSlug || serverSlug === 'all') return
    setLoading(true)
    try {
      const factionApi = normalizeFactionForApi(faction)
      let points = []

      const toTS = p => {
        const raw = p.time ?? p.recorded_at
        if (typeof raw === 'number') return raw
        return Math.floor(new Date(raw).getTime() / 1000)
      }

      const parsed = _parseGroupLabel(serverSlug)

      if (realmName && parsed) {
        // ── Task 4 mode: per-server history from DB ───────────────────────────
        // GET /price-history?server={realm}&region={EU}&version={Anniversary}&faction={f}
        const params = new URLSearchParams({
          server:  realmName,
          region:  parsed.region,
          version: parsed.version,
          faction: factionApi,
          last:    String(period.points),
          hours:   String(period.hours),
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
        // If DB empty or unavailable, fall through to OHLC below
      }

      if (points.length === 0) {
        // ── Legacy mode (group OHLC) ──────────────────────────────────────────
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
        points = data.points ?? []
      }

      // Append live point — always shows current price on right edge
      if (realmName && parsed) {
        const live = await fetchLivePrice(realmName, parsed.region, parsed.version, factionApi)
        if (live) {
          const nowTs = Math.floor(Date.now() / 1000)
          // Only append if live point is newer than last historical point
          const lastTs = points.length > 0
            ? Math.floor(new Date(points[points.length - 1].recorded_at ?? points[points.length - 1].time).getTime() / 1000)
            : 0
          if (nowTs > lastTs) {
            points = [
              ...points,
              {
                recorded_at:        new Date().toISOString(),
                index_price_per_1k: live.index_price_per_1k,
                best_ask:           live.best_ask_per_1k,
                avg_price:          live.index_price_per_1k,
                vwap:               null,
                sources:            [],
              },
            ]
          }
        }
      }

      setEmpty(points.length === 0)
      if (points.length === 0) {
        setLoading(false)
        return
      }

      const conv = v => applyPriceUnit(v, showPer1)

      seriesRef.current.index?.setData(
        points.map(p => ({ time: toTS(p), value: conv(p.avg_price || p.close || 0) }))
      )
      seriesRef.current.ask?.setData(
        points.filter(p => (p.best_ask || 0) > 0)
              .map(p => ({ time: toTS(p), value: conv(p.best_ask) }))
      )

      const allSrc = new Set(points.flatMap(p => p.sources || []))
      setSources([...allSrc])
      chartRef.current?.timeScale().fitContent()
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
        style={{ height: 240, opacity: loading ? 0.6 : 1, transition: 'opacity .25s' }}
      />

      {empty && !loading && (
        <div className={styles.empty}>Нет данных за выбранный период</div>
      )}
    </div>
  )
}
