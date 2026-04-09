import styles from './OffersTable.module.css'
import { buildDisplayList } from '../hooks/useOffers'

/**
 * Sanitize a URL before rendering in an <a href>.
 * Rejects non-http(s) protocols (e.g. javascript:) to prevent XSS.
 * Returns null for missing, blank, or unsafe URLs.
 *
 * @param {string|null|undefined} url
 * @returns {string|null}
 */
export function safeUrl(url) {
  if (!url) return null
  try {
    const u = new URL(url)
    return u.protocol === 'https:' || u.protocol === 'http:' ? url : null
  } catch {
    return null
  }
}

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
    if (Number.isNaN(d.getTime())) return '—'
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return '—'
  }
}

function hexToRgba(hex, alpha = 1) {
  if (!hex || typeof hex !== 'string') return `rgba(255, 255, 255, ${alpha})`
  const clean = hex.replace('#', '')
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean
  const num = parseInt(full, 16)
  if (Number.isNaN(num)) return `rgba(255, 255, 255, ${alpha})`
  const r = (num >> 16) & 255
  const g = (num >> 8) & 255
  const b = num & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/**
 * @deprecated Superseded by getTopPickIds from useOffers.js (Task 3).
 * Kept for backward compatibility — no longer used in OffersTable.
 *
 * Cross-platform top-5 highlight set with per-source guarantee.
 * `sorted` must be sorted ASC by price_per_1k.
 */
export function getTop5Set(sorted) {
  const result = new Set()
  const countBySource = {}
  for (const o of sorted) {
    const src = o.source
    if (!countBySource[src]) countBySource[src] = 0
    if (countBySource[src] < 2) {
      result.add(o.id)
      countBySource[src]++
    }
  }
  for (const o of sorted) {
    if (result.size >= 5) break
    result.add(o.id)
  }
  return result
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

  // Task 3: single pass — top picks pinned first, then remaining.
  // buildDisplayList returns both the ordered list and the topPickIds Set so
  // we never iterate offers twice for the same information.
  const { sorted: displayList, topPickIds } = buildDisplayList(offers)
  // minPrice is always displayList[0] (top picks are sorted cheapest-first)
  const minPrice    = displayList.length > 0 ? displayList[0].price_per_1k : 0

  return (
    <div className={styles.wrapper}>
      <table className={styles.table} aria-label="WoW gold market offers">
        <thead>
          <tr>
            <th className={`${styles.rankCol} ${styles.hideOnMobile}`}>#</th>
            <th>Платформа</th>
            <th>Сервер · Фракция</th>
            <th className={styles.right}>{showPer1 ? 'Цена / 1' : 'Цена / 1K'}</th>
            <th className={`${styles.right} ${styles.hideOnMobile}`}>Position Value</th>
            <th className={styles.right}>Объём</th>
            <th>Продавец</th>
            <th className={`${styles.right} ${styles.hideOnMobile}`}>Обновлено</th>
            <th className={styles.actionCol}></th>
          </tr>
        </thead>
        <tbody>
          {displayList.map((offer, i) => {
            const isBest      = offer.price_per_1k === minPrice
            const isTopPick   = topPickIds.has(offer.id)
            const rank        = i + 1
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
              isBest    ? styles.best    : '',       // cheapest overall: green accent
            ].filter(Boolean).join(' ')
            const topPickRowStyle = isTopPick
              ? {
                  backgroundColor: `${hexToRgba(src.color, 0.08)}`,
                  boxShadow: `inset 3px 0 0 ${src.color}`,
                }
              : undefined

            return (
              <tr key={`${offer.source}-${offer.id}`} className={rowCls} style={topPickRowStyle}>

                {/* Ранг: последовательный номер; для top picks — ★N */}
                <td className={`${styles.rank} ${styles.hideOnMobile}`}>
                  {isTopPick
                    ? <span className={styles.crown} title="Top Pick">{`★${rank}`}</span>
                    : <span className={styles.rankNum}>{rank}</span>
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
                    <td className={`${styles.right} ${styles.hideOnMobile} mono${isHuge ? ` ${styles.posHuge}` : ''}`}>
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
                <td className={`${styles.time} ${styles.right} ${styles.hideOnMobile} mono`}>
                  {formatTime(offer.updated_at)}
                </td>

                {/* Основное действие */}
                <td className={styles.actionCell}>
                  {safeUrl(offer.offer_url)
                    ? (
                      <a
                        href={safeUrl(offer.offer_url)}
                        target="_blank"
                        rel="noreferrer"
                        className={`${styles.buyBtn} ${isBest ? styles.buyBtnBest : ''}`}
                        style={isTopPick ? { color: src.color, borderColor: src.color } : undefined}
                        title={offer.is_suspicious ? 'Цена значительно выше рыночной' : undefined}
                        aria-label={`Купить у ${offer.seller} на ${src.label}`}
                      >
                        {offer.is_suspicious ? '⚠ Купить' : 'Купить'}
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
