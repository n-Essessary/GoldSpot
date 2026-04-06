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

export function PriceChart({ serverSlug, refreshSignal }) {
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

  // ── Загрузка данных ────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!serverSlug || serverSlug === 'all') return
    setLoading(true)
    try {
      const params = new URLSearchParams({
        server:     serverSlug,
        faction:    faction.toLowerCase(),
        last_hours: String(period.hours),
        max_points: String(period.points),
      })
      const res = await fetch(`${API_BASE}/price-history/ohlc?${params}`)
      if (!res.ok) return   // сетевая ошибка — не очищаем граф

      const data   = await res.json()
      const points = data.points ?? []

      setEmpty(points.length === 0)
      if (points.length === 0) return

      // time приходит как ISO string из asyncpg
      const toTS = p => Math.floor(new Date(p.time).getTime() / 1000)

      seriesRef.current.index?.setData(
        points.map(p => ({ time: toTS(p), value: p.avg_price || p.close || 0 }))
      )
      seriesRef.current.vwap?.setData(
        points.filter(p => (p.vwap || 0) > 0)
              .map(p => ({ time: toTS(p), value: p.vwap }))
      )
      seriesRef.current.ask?.setData(
        points.filter(p => (p.best_ask || 0) > 0)
              .map(p => ({ time: toTS(p), value: p.best_ask }))
      )

      const allSrc = new Set(points.flatMap(p => p.sources || []))
      setSources([...allSrc])
      chartRef.current?.timeScale().fitContent()
    } catch {
      // сетевой сбой — граф остаётся со старыми данными, loading скрывается
    } finally {
      setLoading(false)
    }
  }, [serverSlug, faction, period])

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
