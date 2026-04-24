import { useMemo } from 'react'
import styles from './StatsBar.module.css'

/**
 * TradingView-style stat cards bar.
 *
 * Cards: FunPay count · G2G count · Best FunPay · Best G2G · Spread · Volume · Median
 *
 * @param {{
 *   offers: import('../api/offers').Offer[],
 *   loading: boolean,
 *   priceUnit?: 'per_1k'|'per_1m',
 * }} props
 */
export function StatsBar({ offers, loading, priceUnit = 'per_1k' }) {
  const stats = useMemo(() => computeStats(offers), [offers])
  const multiplier = priceUnit === 'per_1m' ? 1000 : 1

  const fmt = (v) => {
    if (v === null || v === undefined) return '—'
    const n = v * multiplier
    if (n >= 1)    return `$${n.toFixed(2)}`
    if (n >= 0.01) return `$${n.toFixed(4)}`
    return `$${n.toFixed(6)}`
  }

  const fmtSpread = (v) => {
    if (!v || v.value === null || v.value === undefined) return '—'
    const n = v.value * multiplier
    let money = ''
    if (Math.abs(n) >= 1) money = `$${Math.abs(n).toFixed(2)}`
    else if (Math.abs(n) >= 0.01) money = `$${Math.abs(n).toFixed(4)}`
    else money = `$${Math.abs(n).toFixed(6)}`
  const price1 = Number(v.price1)
  const price2 = Number(v.price2)
  const denom = Math.max(price1, price2)
  const pct = denom > 0
    ? Math.abs((price1 - price2) / denom * 100).toFixed(1)
    : '0.0'
  return `${v.winner} cheaper by ${money} (${pct}%)`
  }

  return (
    <div className={`${styles.bar} ${loading ? styles.faded : ''}`}>
      {/* Source counts */}
      <div className={styles.card}>
        <span className={`${styles.label} ${styles.funpayLabel}`}>FunPay</span>
        <span className={`${styles.value} ${styles.funpayValue}`}>{stats.funpayCount}</span>
      </div>
      <div className={styles.card}>
        <span className={`${styles.label} ${styles.g2gLabel}`}>G2G</span>
        <span className={`${styles.value} ${styles.g2gValue}`}>{stats.g2gCount}</span>
      </div>

      <div className={styles.divider} />

      {/* Best prices per source */}
      <div className={styles.card}>
        <span className={`${styles.label} ${styles.funpayLabel}`}>Лучший FunPay</span>
        <span className={`${styles.value} ${styles.bestValue} mono`}>{fmt(stats.bestFunpay)}</span>
      </div>
      <div className={styles.card}>
        <span className={`${styles.label} ${styles.g2gLabel}`}>Лучший G2G</span>
        <span className={`${styles.value} ${styles.bestValue} mono`}>{fmt(stats.bestG2g)}</span>
      </div>

      <div className={styles.divider} />

      {/* Spread */}
      <div className={styles.card}>
        <span className={`${styles.label} ${styles.spreadLabel}`}>Спред</span>
        <span className={`${styles.value} ${styles.spreadValue} mono`}>{fmtSpread(stats.spread)}</span>
      </div>

      <div className={styles.divider} />

      {/* Market-wide stats */}
      <div className={styles.card}>
        <span className={styles.label}>Медиана</span>
        <span className={`${styles.value} mono`}>{fmt(stats.median)}</span>
      </div>
      <div className={styles.card}>
        <span className={styles.label}>Объём (∑)</span>
        <span className={`${styles.value} mono`}>
          {stats.totalGold !== null ? stats.totalGold.toLocaleString('ru-RU') : '—'}
        </span>
      </div>
    </div>
  )
}

function computeStats(offers) {
  // Filter zero/invalid prices to avoid $0.00 artifacts
  const valid = offers.filter(o => o.price_per_1k > 0)

  if (!valid.length) {
    return {
      funpayCount: 0,
      g2gCount:    0,
      bestFunpay:  null,
      bestG2g:     null,
      spread:      null,
      totalGold:   null,
      median:      null,
    }
  }

  const funpayOffers = valid.filter(o => o.source === 'funpay')
  const g2gOffers    = valid.filter(o => o.source === 'g2g')

  const bestFunpay = funpayOffers.length
    ? Math.min(...funpayOffers.map(o => o.price_per_1k))
    : null

  const bestG2g = g2gOffers.length
    ? Math.min(...g2gOffers.map(o => o.price_per_1k))
    : null

  const spread = bestFunpay !== null && bestG2g !== null
    ? {
        value: Math.abs(bestG2g - bestFunpay),
        winner: bestG2g < bestFunpay ? 'G2G' : 'FunPay',
        price1: bestFunpay,
        price2: bestG2g,
      }
    : null

  const prices  = valid.map(o => o.price_per_1k).sort((a, b) => a - b)
  const midIdx  = Math.floor(prices.length / 2)
  const median  = prices.length % 2 === 0
    ? (prices[midIdx - 1] + prices[midIdx]) / 2
    : prices[midIdx]

  const totalGold = valid.reduce((s, o) => s + o.amount_gold, 0)

  return {
    funpayCount: funpayOffers.length,
    g2gCount:    g2gOffers.length,
    bestFunpay,
    bestG2g,
    spread,
    totalGold,
    median,
  }
}
