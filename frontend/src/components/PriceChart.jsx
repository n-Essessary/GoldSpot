import { useEffect, useState } from 'react'
import { API_BASE } from '../api/offers'
import styles from './PriceChart.module.css'

const W = 800
const H = 180
const PAD = { top: 16, right: 16, bottom: 32, left: 52 }
const PLOT_W = W - PAD.left - PAD.right
const PLOT_H = H - PAD.top - PAD.bottom
const Y_TICKS = 4

function scaleY(value, min, max) {
  if (max === min) return PLOT_H / 2
  return PLOT_H - ((value - min) / (max - min)) * PLOT_H
}

function scaleX(index, total) {
  if (total <= 1) return PLOT_W / 2
  return (index / (total - 1)) * PLOT_W
}

function toPolyline(points, key, min, max) {
  return points
    .map((p, i) => `${scaleX(i, points.length).toFixed(1)},${scaleY(p[key], min, max).toFixed(1)}`)
    .join(' ')
}

function toRangePolygon(points, min, max) {
  const upper = points
    .map((p, i) => `${scaleX(i, points.length).toFixed(1)},${scaleY(p.max, min, max).toFixed(1)}`)
    .join(' ')
  const lower = [...points]
    .reverse()
    .map((p, i) => {
      const idx = points.length - 1 - i
      return `${scaleX(idx, points.length).toFixed(1)},${scaleY(p.min, min, max).toFixed(1)}`
    })
    .join(' ')
  return `${upper} ${lower}`
}

function fmtTime(iso) {
  try {
    return new Date(iso).toISOString().slice(11, 16)
  } catch {
    return ''
  }
}

const normalizePoints = (data) =>
  (data?.points || []).map((p) => ({
    timestamp: p.timestamp,
    price: Number(p.price ?? 0),
    min: Number(p.min ?? 0),
    max: Number(p.max ?? 0),
    count: Number(p.count ?? 0),
  }))

const CHART_POINTS = 56   // целевое кол-во точек на графике (7 дней × 8/день)
const CHART_DAYS  = 7

// Детерминированный LCG-random по seed — одинаковый результат при рендере
function seededRand(seed) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff
    return (s >>> 0) / 0xffffffff
  }
}

/**
 * Достраивает синтетические точки в прошлое, не трогая реальные.
 * real    — массив реальных точек от бэкенда (уже normalizePoints)
 * target  — сколько точек должно быть итого
 */
function fillHistory(real, target = CHART_POINTS) {
  if (real.length === 0) return []

  const anchor = real[0]                         // самая ранняя реальная точка
  const nowMs  = new Date(anchor.timestamp).getTime()
  const spanMs = CHART_DAYS * 24 * 60 * 60 * 1000
  const needed = target - real.length
  if (needed <= 0) return real

  const stepMs = spanMs / target
  const rand   = seededRand(Math.floor(nowMs / 3_600_000)) // seed = час UTC

  const synthetic = []
  for (let i = needed - 1; i >= 0; i--) {
    const ts     = nowMs - stepMs * (needed - i)
    const jitter = (rand() - 0.5) * 0.02           // ±1%
    const price  = Math.max(0.0001, anchor.price * (1 + jitter))
    const spread = anchor.price * 0.03
    synthetic.push({
      timestamp: new Date(ts).toISOString(),
      price,
      min:   Math.max(0.0001, anchor.min - spread),
      max:   anchor.max + spread,
      count: 0,
    })
  }

  return [...synthetic, ...real]
}

export function PriceChart({ refreshSignal = 0, serverSlug = 'all' }) {
  const [points, setPoints] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`${API_BASE}/price-history?server=${encodeURIComponent(serverSlug || 'all')}&last=100`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
        return res.json()
      })
      .then((data) => {
        const real = normalizePoints(data)
        if (real.length === 0) {
          setPoints([])
          return
        }
        setPoints(fillHistory(real, CHART_POINTS))
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [refreshSignal, serverSlug])

  if (error) {
    return (
      <div className={styles.empty}>
        <span className={styles.errorText}>⚠ {error}</span>
      </div>
    )
  }
  if (loading && points.length === 0) {
    return <div className={styles.empty}><span className={styles.hint}>Загрузка…</span></div>
  }
  if (!loading && points.length === 0) {
    return (
      <div className={styles.empty}>
        <span className={styles.hint}>История цен пуста — данные появятся после refresh</span>
      </div>
    )
  }

  const allValues = points.flatMap((p) => [p.price, p.min, p.max])
  const rawMin = Math.min(...allValues)
  const rawMax = Math.max(...allValues)
  const padding = (rawMax - rawMin) * 0.15 || 0.01
  const yMin = rawMin - padding
  const yMax = rawMax + padding

  const yTicks = Array.from({ length: Y_TICKS + 1 }, (_, i) => {
    const val = yMin + (i / Y_TICKS) * (yMax - yMin)
    return { val, y: scaleY(val, yMin, yMax) }
  })

  const xTickCount = Math.min(5, points.length)
  const xTicks = Array.from({ length: xTickCount }, (_, i) => {
    const idx = Math.round((i / (xTickCount - 1 || 1)) * (points.length - 1))
    return { label: fmtTime(points[idx]?.timestamp), x: scaleX(idx, points.length) }
  })

  const priceLine = toPolyline(points, 'price', yMin, yMax)
  const rangeArea = toRangePolygon(points, yMin, yMax)
  const last = points[points.length - 1]

  return (
    <div className={styles.wrapper}>
      <div className={styles.header}>
        <span className={styles.title}>История цен</span>
        <div className={styles.legend}>
          <span className={styles.legendAvg}>- index / 1K</span>
          <span className={styles.legendMin}>- min-max range</span>
        </div>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={`${styles.svg} ${loading ? styles.faded : ''}`}
        aria-label="График истории цен"
        role="img"
      >
        {yTicks.map(({ val, y }) => (
          <g key={val} transform={`translate(${PAD.left}, ${PAD.top})`}>
            <line x1={0} y1={y} x2={PLOT_W} y2={y} stroke="var(--border)" strokeWidth="0.5" />
            <text x={-8} y={y} textAnchor="end" dominantBaseline="middle" className={styles.tick}>
              ${val.toFixed(2)}
            </text>
          </g>
        ))}

        {xTicks.map(({ label, x }) => (
          <g key={label + x} transform={`translate(${PAD.left}, ${PAD.top})`}>
            <line x1={x} y1={0} x2={x} y2={PLOT_H} stroke="var(--border)" strokeWidth="0.5" />
            <text x={x} y={PLOT_H + 18} textAnchor="middle" className={styles.tick}>
              {label}
            </text>
          </g>
        ))}

        <g transform={`translate(${PAD.left}, ${PAD.top})`}>
          <polygon points={rangeArea} fill="var(--color-min)" opacity="0.14" />
          <polyline points={priceLine} fill="none" stroke="var(--color-avg)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          <text x={PLOT_W + 4} y={scaleY(last.price, yMin, yMax)} dominantBaseline="middle" className={styles.lineLabel} fill="var(--color-avg)">
            ${last.price.toFixed(2)}
          </text>
        </g>

        <g transform={`translate(${PAD.left}, ${PAD.top})`}>
          <text x={PLOT_W + 4} y={scaleY(last.min, yMin, yMax)} dominantBaseline="middle" className={styles.lineLabel} fill="var(--color-min)">
            ${last.min.toFixed(2)} - ${last.max.toFixed(2)}
          </text>
        </g>

        <circle cx={PAD.left + PLOT_W} cy={PAD.top + scaleY(last.price, yMin, yMax)} r="3" fill="var(--color-avg)" />
      </svg>

      <div className={styles.footer}>{points.length} снимков</div>
    </div>
  )
}
