import styles from './StatsBar.module.css'

/**
 * @param {{ offers: import('../api/offers').Offer[], loading: boolean }} props
 */
export function StatsBar({ offers, loading }) {
  const stats = computeStats(offers)

  return (
    <div className={`${styles.bar} ${loading ? styles.faded : ''}`}>
      <Stat label="офферов" value={stats.count} mono={false} />
      <div className={styles.divider} />
      <Stat label="мин. цена" value={stats.min !== null ? `$${stats.min.toFixed(2)}` : '—'} />
      <Stat label="ср. цена" value={stats.avg !== null ? `$${stats.avg.toFixed(2)}` : '—'} />
      <Stat label="макс. цена" value={stats.max !== null ? `$${stats.max.toFixed(2)}` : '—'} />
      <div className={styles.divider} />
      <Stat label="объем (сумм.)" value={stats.totalGold !== null ? stats.totalGold.toLocaleString('ru-RU') : '—'} />
    </div>
  )
}

function Stat({ label, value, mono = true }) {
  return (
    <div className={styles.stat}>
      <span className={styles.label}>{label}</span>
      <span className={`${styles.value} ${mono ? 'mono' : ''}`}>{value}</span>
    </div>
  )
}

function computeStats(offers) {
  if (!offers.length) {
    return { count: 0, min: null, avg: null, max: null, totalGold: null }
  }

  let min = Infinity
  let max = -Infinity
  let sum = 0
  let totalGold = 0

  for (const offer of offers) {
    const price = offer.price_per_1k
    if (price < min) min = price
    if (price > max) max = price
    sum += price
    totalGold += offer.amount_gold
  }

  return {
    count: offers.length,
    min,
    avg: sum / offers.length,
    max,
    totalGold,
  }
}
