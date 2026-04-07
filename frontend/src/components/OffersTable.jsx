import styles from './OffersTable.module.css'

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

/**
 * Smart price formatter — handles the full range from per-1 ($0.000005) to per-1k ($10.00).
 * Adapts decimal places so small FunPay prices are never shown as $0.00.
 */
export function formatPrice(v) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  if (v >= 1)    return `$${v.toFixed(2)}`
  if (v >= 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(6)}`
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

/**
 * Top-5 highlight set by global cheapest price.
 * Returns a Set of offer IDs. `sorted` must already be sorted ASC by price_per_1k.
 */
export function getTop5Set(sorted) {
  return new Set(sorted.slice(0, 5).map(o => o.id))
}

/**
 * @param {{
 *   offers: import('../api/offers').Offer[],
 *   loading: boolean,
 *   error: string|null,
 *   currentServer?: string   — display_server текущей страницы (для G2G fallback)
 *   showPer1?: boolean        — display price per 1 gold instead of per 1k
 * }} props
 */
export function OffersTable({ offers, loading, error, currentServer = '', showPer1 = false }) {
  // TODO(I5): virtualize rows (react-window) when offers.length > 100.
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
  const sorted   = [...offers].sort((a, b) => a.price_per_1k - b.price_per_1k)
  const minPrice = sorted[0].price_per_1k
  const top5Ids  = getTop5Set(sorted)

  return (
    <div className={styles.wrapper}>
      <table className={styles.table} aria-label="WoW gold market offers">
        <thead>
          <tr>
            <th className={styles.rankCol}>#</th>
            <th>Платформа</th>
            <th>Сервер · Фракция</th>
            <th className={styles.right}>{showPer1 ? 'Цена / 1' : 'Цена / 1K'}</th>
            <th className={styles.right}>Position Value</th>
            <th className={styles.right}>Объём</th>
            <th>Продавец</th>
            <th className={styles.right}>Обновлено</th>
            <th className={styles.actionCol}></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((offer, i) => {
            const isBest      = offer.price_per_1k === minPrice
            const isTop5      = top5Ids.has(offer.id)
            const src         = SOURCE_META[offer.source] ?? { label: offer.source, color: 'var(--text-secondary)' }
            // ⚠ Аномально дорогой G2G-оффер: >= 3× от min_price сервера
            const isExpensive = offer.source === 'g2g' && offer.price_per_1k >= minPrice * 3
            // Сервер в ячейке: реалм (G2G) или fallback на текущую группу (серый)
            const realmLabel  = offer.server_name || null
            const serverLabel = realmLabel ?? currentServer
            // Displayed price value depending on unit mode
            const displayPrice = showPer1 ? offer.price_per_1k / 1000 : offer.price_per_1k

            const rowCls = [
              styles.row,
              isBest ? styles.best : '',
              isTop5 && !isBest ? styles.top5 : '',
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
                  {serverLabel && (
                    <span className={realmLabel ? styles.server : styles.serverFallback}>
                      {serverLabel}
                    </span>
                  )}
                  <span
                    className={styles.faction}
                    style={{ color: FACTION_COLOR[offer.faction] ?? 'var(--text-secondary)' }}
                  >
                    {offer.faction}
                  </span>
                </td>

                {/* Цена — главная цифра */}
                <td className={`${styles.price} ${styles.right} mono`}>
                  {isExpensive && (
                    <span
                      className={styles.priceWarn}
                      title="Цена значительно выше рынка"
                    >
                      ⚠
                    </span>
                  )}
                  {formatPrice(displayPrice)}
                </td>

                {/* Position Value = price_per_1k × (amount_gold / 1000) */}
                {(() => {
                  const positionValue = offer.price_per_1k * (offer.amount_gold / 1000)
                  const isHuge = positionValue > 9999
                  return (
                    <td className={`${styles.right} mono${isHuge ? ` ${styles.posHuge}` : ''}`}>
                      {isHuge ? '∞' : `$${positionValue.toFixed(2)}`}
                    </td>
                  )
                })()}

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
