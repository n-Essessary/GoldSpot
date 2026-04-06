// PriceChart.jsx — TradingView lightweight-charts area chart for price history.
// Requires: npm install lightweight-charts
import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, ColorType, LineStyle } from 'lightweight-charts'
import { API_BASE } from '../api/offers'
import styles from './PriceChart.module.css'

const PERIODS = [
  { label: '1H',  hours: 1,   bucket: 5    },
  { label: '6H',  hours: 6,   bucket: 15   },
  { label: '24H', hours: 24,  bucket: 60   },
  { label: '7D',  hours: 168, bucket: 360  },
  { label: '30D', hours: 720, bucket: 1440 },
]

const FACTIONS = ['All', 'Alliance', 'Horde']

export function PriceChart({ serverSlug, factionSlug, refreshSignal }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef({})
  const [period,  setPeriod]  = useState(PERIODS[2])   // 24H default
  const [faction, setFaction] = useState('All')
  const [loading, setLoading] = useState(false)
  const [empty,   setEmpty]   = useState(false)

  // ── Chart initialisation ──────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background:  { type: ColorType.Solid, color: 'transparent' },
        textColor:   'rgba(156,154,146,1)',
        fontFamily:  'var(--font-mono, ui-monospace, monospace)',
        fontSize:    11,
      },
      grid: {
        vertLines: { color: 'rgba(156,154,146,0.08)' },
        horzLines: { color: 'rgba(156,154,146,0.08)' },
      },
      crosshair: {
        mode:     1,
        vertLine: { color: 'rgba(156,154,146,0.4)', style: LineStyle.Dashed },
        horzLine: { color: 'rgba(156,154,146,0.4)', style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor:  'rgba(156,154,146,0.15)',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor:    'rgba(156,154,146,0.15)',
        timeVisible:    true,
        secondsVisible: false,
        fixLeftEdge:    true,
        fixRightEdge:   true,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale:  { mouseWheel: true, pinch: true },
    })

    // Min boundary (no fill, subtle line)
    seriesRef.current.min = chart.addAreaSeries({
      lineColor:        'rgba(30,157,117,0.3)',
      topColor:         'rgba(30,157,117,0.0)',
      bottomColor:      'rgba(30,157,117,0.0)',
      lineWidth:        1,
      priceLineVisible: false,
      lastValueVisible: false,
    })

    // Avg — primary line with gradient fill
    seriesRef.current.avg = chart.addAreaSeries({
      lineColor:        '#1D9E75',
      topColor:         'rgba(30,157,117,0.15)',
      bottomColor:      'rgba(30,157,117,0.0)',
      lineWidth:        2,
      priceLineVisible: true,
      priceLineColor:   'rgba(30,157,117,0.5)',
      lastValueVisible: true,
    })

    // Max boundary (no fill, subtle line)
    seriesRef.current.max = chart.addAreaSeries({
      lineColor:        'rgba(30,157,117,0.3)',
      topColor:         'rgba(30,157,117,0.0)',
      bottomColor:      'rgba(30,157,117,0.0)',
      lineWidth:        1,
      priceLineVisible: false,
      lastValueVisible: false,
    })

    chartRef.current = chart

    // Responsive width via ResizeObserver
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        chart.applyOptions({ width: e.contentRect.width })
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

  // ── Data loading ──────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!serverSlug || serverSlug === 'all') return
    setLoading(true)
    setEmpty(false)
    try {
      const params = new URLSearchParams({
        server:         serverSlug,
        faction:        faction.toLowerCase(),
        last_hours:     String(period.hours),
        bucket_minutes: String(period.bucket),
      })
      const res  = await fetch(`${API_BASE}/price-history/ohlc?${params}`)
      const data = await res.json()
      const points = data.points ?? []

      if (points.length === 0) {
        setEmpty(true)
        // Clear series so stale data doesn't linger
        seriesRef.current.avg?.setData([])
        seriesRef.current.min?.setData([])
        seriesRef.current.max?.setData([])
        return
      }

      // bucket comes back as ISO string from asyncpg
      const toTS = p => Math.floor(new Date(p.bucket ?? p.timestamp).getTime() / 1000)

      seriesRef.current.avg?.setData(
        points.map(p => ({ time: toTS(p), value: Number(p.avg_price ?? p.price ?? 0) }))
      )
      seriesRef.current.min?.setData(
        points.map(p => ({ time: toTS(p), value: Number(p.min_price ?? p.min ?? 0) }))
      )
      seriesRef.current.max?.setData(
        points.map(p => ({ time: toTS(p), value: Number(p.max_price ?? p.max ?? 0) }))
      )
      chartRef.current?.timeScale().fitContent()
    } catch (e) {
      console.error('PriceChart load error:', e)
    } finally {
      setLoading(false)
    }
  }, [serverSlug, faction, period])

  // Re-load whenever filters or refreshSignal change
  useEffect(() => { loadData() }, [loadData, refreshSignal])

  return (
    <div className={styles.wrapper}>
      <div className={styles.controls}>
        <div className={styles.btnGroup}>
          {PERIODS.map(p => (
            <button
              key={p.label}
              className={period.label === p.label ? styles.btnActive : styles.btn}
              onClick={() => setPeriod(p)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className={styles.btnGroup}>
          {FACTIONS.map(f => (
            <button
              key={f}
              className={faction === f ? styles.btnActive : styles.btn}
              onClick={() => setFaction(f)}
            >
              {f}
            </button>
          ))}
        </div>
        {loading && <span className={styles.hint}>Загрузка…</span>}
      </div>

      <div
        ref={containerRef}
        className={styles.chart}
        style={{ height: 240, opacity: loading ? 0.5 : 1, transition: 'opacity .2s' }}
      />

      {empty && !loading && (
        <div className={styles.empty}>Нет данных за выбранный период</div>
      )}
    </div>
  )
}
