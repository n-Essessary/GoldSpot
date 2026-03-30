import styles from './OffersTable.module.css'
import { normalizeServer } from '../utils/server'

// Цвета фракций
const FACTION_COLOR = {
  Horde:   '#ff4d6a',
  Alliance: '#4d9fff',
}

// Метки платформ — source как читаемое имя + акцентный цвет
const SOURCE_META = {
  fanpay:   { label: 'FanPay',   color: '#f59e0b' },
  funpay:   { label: 'FunPay',   color: '#22c55e' },
  g2g:      { label: 'G2G',      color: '#a78bfa' },
  eldorado: { label: 'Eldorado', color: '#34d399' },
}

function formatPrice(n) {
  return `$${n.toFixed(2)}`
}

function formatGold(n) {
  return n.toLocaleString('ru-RU')
}

// "2025-01-15T10:30:00Z" → "10:30"
function formatTime(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? '—' : d.toISOString().slice(11, 16)
  } catch {
    return '—'
  }
}

// Порог для топ-3 подсветки
function getTop3Threshold(offers) {
  if (offers.length === 0) return Infinity
  const sorted = [...offers].map(o => o.price_per_1k).sort((a, b) => a - b)
  return sorted[Math.min(2, sorted.length - 1)]
}

/**
 * @param {{ offers: import('../api/offers').Offer[], loading: boolean, error: string|null }} props
 */
export function OffersTable({ offers, loading, error }) {
  if (error) {
    return (
      <div className={styles.state}>
        <span className={styles.errorIcon}>⚠</span>
        <span className={styles.errorText}>{error}</span>
      </div>
    )
  }

  if (loading && offers.length === 0) {
    return (
      <div className={styles.state}>
        <div className={styles.spinner} />
        <span className={styles.hint}>Загрузка предложений…</span>
      </div>
    )
  }

  if (!loading && offers.length === 0) {
    return (
      <div className={styles.state}>
        <span className={styles.hint}>Предложений не найдено</span>
      </div>
    )
  }

  // Сортируем по цене ASC — дешёвые сверху
  const sorted       = [...offers].sort((a, b) => a.price_per_1k - b.price_per_1k)
  const minPrice     = sorted[0].price_per_1k
  const top3Thr      = getTop3Threshold(sorted)

  return (
    <div className={styles.wrapper}>
      <table className={styles.table} aria-label="WoW gold market offers">
        <thead>
          <tr>
            <th className={styles.rankCol}>#</th>
            <th>Платформа</th>
            <th>Сервер · Фракция</th>
            <th className={styles.right}>Цена / 1K</th>
            <th className={styles.right}>Объём</th>
            <th>Продавец</th>
            <th className={styles.right}>Обновлено</th>
            <th className={styles.actionCol}></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((offer, i) => {
            const isBest = offer.price_per_1k === minPrice
            const isTop3 = !isBest && offer.price_per_1k <= top3Thr
            const src    = SOURCE_META[offer.source] ?? { label: offer.source, color: 'var(--text-secondary)' }

            const rowCls = [
              styles.row,
              isBest ? styles.best : '',
              isTop3 ? styles.top3 : '',
            ].filter(Boolean).join(' ')

            return (
              <tr key={`${offer.source}-${offer.id}`} className={rowCls}>

                {/* Ранг */}
                <td className={styles.rank}>
                  {isBest
                    ? <span className={styles.crown} title="Лучшая цена">★</span>
                    : <span className={styles.rankNum}>{i + 1}</span>
                  }
                </td>

                {/* Платформа — главный акцент */}
                <td>
                  <span
                    className={styles.source}
                    style={{ '--src-color': src.color }}
                  >
                    {src.label}
                  </span>
                </td>

                {/* Сервер + Фракция в одной ячейке */}
                <td className={styles.serverCell}>
                  <span className={styles.server}>{normalizeServer(offer.server).label}</span>
                  <span
                    className={styles.faction}
                    style={{ color: FACTION_COLOR[offer.faction] ?? 'var(--text-secondary)' }}
                  >
                    {offer.faction}
                  </span>
                </td>

                {/* Цена — главная цифра */}
                <td className={`${styles.price} ${styles.right} mono`}>
                  {formatPrice(offer.price_per_1k)}
                </td>

                {/* Объём */}
                <td className={`${styles.gold} ${styles.right} mono`}>
                  {formatGold(offer.amount_gold)}
                </td>

                {/* Продавец */}
                <td className={styles.seller}>{offer.seller}</td>

                {/* Время последнего обновления */}
                <td className={`${styles.time} ${styles.right} mono`}>
                  {formatTime(offer.updated_at)}
                </td>

                {/* Основное действие */}
                <td className={styles.actionCell}>
                  {offer.offer_url
                    ? (
                      <a
                        href={offer.offer_url}
                        target="_blank"
                        rel="noreferrer"
                        className={`${styles.buyBtn} ${isBest ? styles.buyBtnBest : ''}`}
                        aria-label={`Купить у ${offer.seller} на ${src.label}`}
                      >
                        Купить
                      </a>
                    )
                    : <span className={styles.noLink}>—</span>
                  }
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {loading && <div className={styles.loadingBar} />}
    </div>
  )
}
