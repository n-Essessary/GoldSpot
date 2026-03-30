import { useEffect, useState } from 'react'
import { fetchPriceHistory } from '../api/offers'
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

function fmtTime(iso) {
  try {
    return new Date(iso).toISOString().slice(11, 16)
  } catch {
    return ''
  }
}

export function PriceChart({ refreshSignal = 0 }) {
  const [points, setPoints] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchPriceHistory({ last: 100 })
      .then(setPoints)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [refreshSignal])

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

  const allValues = points.flatMap((p) => [p.avg_price, p.min_price])
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

  const avgLine = toPolyline(points, 'avg_price', yMin, yMax)
  const minLine = toPolyline(points, 'min_price', yMin, yMax)
  const last = points[points.length - 1]

  return (
    <div className={styles.wrapper}>
      <div className={styles.header}>
        <span className={styles.title}>История цен</span>
        <div className={styles.legend}>
          <span className={styles.legendAvg}>- avg / 1K</span>
          <span className={styles.legendMin}>- min / 1K</span>
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
          <polyline points={avgLine} fill="none" stroke="var(--color-avg)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          <text x={PLOT_W + 4} y={scaleY(last.avg_price, yMin, yMax)} dominantBaseline="middle" className={styles.lineLabel} fill="var(--color-avg)">
            ${last.avg_price.toFixed(2)}
          </text>
        </g>

        <g transform={`translate(${PAD.left}, ${PAD.top})`}>
          <polyline points={minLine} fill="none" stroke="var(--color-min)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
          <text x={PLOT_W + 4} y={scaleY(last.min_price, yMin, yMax)} dominantBaseline="middle" className={styles.lineLabel} fill="var(--color-min)">
            ${last.min_price.toFixed(2)}
          </text>
        </g>

        <circle cx={PAD.left + PLOT_W} cy={PAD.top + scaleY(last.avg_price, yMin, yMax)} r="3" fill="var(--color-avg)" />
        <circle cx={PAD.left + PLOT_W} cy={PAD.top + scaleY(last.min_price, yMin, yMax)} r="3" fill="var(--color-min)" />
      </svg>

      <div className={styles.footer}>{points.length} снимков</div>
    </div>
  )
}
