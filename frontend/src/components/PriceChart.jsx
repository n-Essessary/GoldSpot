/**
 * PriceChart.jsx — TradingView lightweight-charts.
 * Три линии: index_price (VW-Median), vwap, best_ask.
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

const FACTIONS = ['All', 'Alliance', 'Horde']

/** API values are per 1k gold; Per 1 divides by 1000. */
export const applyPriceUnit = (valuePer1k, showPer1) =>
  showPer1 ? valuePer1k / 1000 : valuePer1k

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
 * PriceChart — TradingView lightweight-charts.
 *
 * Props:
 *   serverSlug    — display_server group label, e.g. "(EU) Anniversary" (required)
 *   refreshSignal — bumped on data updates to trigger re-fetch
 *   realmName     — specific realm, e.g. "Firemaw" (optional, Task 4)
 *                   When set → fetches per-server DB history
 *                   When unset → fetches legacy group OHLC from price_index_snapshots
 */
export function PriceChart({ serverSlug, refreshSignal, realmName, showPer1 = false }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef({})
  const [period,  setPeriod]  = useState(PERIODS[2])   // 24H default
  const [faction, setFaction] = useState('All')
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

    // vwap — пунктир синий
    seriesRef.current.vwap = chart.addLineSeries({
      color:                  'rgba(55,138,221,0.75)',
      lineWidth:              1,
      lineStyle:              LineStyle.Dashed,
      crosshairMarkerVisible: false,
      priceLineVisible:       false,
      lastValueVisible:       true,
      title:                  'VWAP',
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

    // Crosshair tooltip — keeps scale/labels active; lastValueVisible shows series values
    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.seriesData) return
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
          faction,
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
              vwap:      p.vwap ?? p.index_price_per_1k,
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
          faction,
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

      setEmpty(points.length === 0)
      if (points.length === 0) {
        setLoading(false)
        return
      }

      const conv = v => applyPriceUnit(v, showPer1)

      seriesRef.current.index?.setData(
        points.map(p => ({ time: toTS(p), value: conv(p.avg_price || p.close || 0) }))
      )
      seriesRef.current.vwap?.setData(
        points.filter(p => (p.vwap || 0) > 0)
              .map(p => ({ time: toTS(p), value: conv(p.vwap) }))
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
        <div className={styles.group}>
          {FACTIONS.map(f => (
            <button
              key={f}
              className={faction === f ? styles.active : styles.btn}
              onClick={() => setFaction(f)}
            >
              {f}
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
