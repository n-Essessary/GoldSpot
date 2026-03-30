import styles from './SourceSummary.module.css'

// Параметры бейджей платформ
const SOURCE_META = {
  fanpay: { label: 'FanPay', color: '#f59e0b' },
  g2g: { label: 'G2G', color: '#a78bfa' },
  eldorado: { label: 'Eldorado', color: '#34d399' },
}

function formatPrice(n) {
  return `$${Number(n).toFixed(2)}`
}

/**
 * Платформы-фильтр (cards вместо чекбоксов).
 * Важно: компонента получает ALL offers, чтобы карточки и агрегаты
 * считались по всем офферам, даже если конкретная платформа отключена.
 *
 * @param {{
 *  offers: import('../api/offers').Offer[],
 *  enabledSources: Set<string>|null,
 *  toggleSource: (source: string)=>void
 * }} props
 */
export function SourceSummary({ offers, enabledSources, toggleSource }) {
  const sources = Array.from(
    new Set(offers.map((o) => o.source).filter(Boolean)),
  ).sort()

  const minBySource = new Map()
  const countBySource = new Map()

  for (const o of offers) {
    countBySource.set(o.source, (countBySource.get(o.source) ?? 0) + 1)
    const curMin = minBySource.get(o.source)
    const p = o.price_per_1k
    if (curMin == null || p < curMin) minBySource.set(o.source, p)
  }

  return (
    <section className={styles.wrapper} aria-label="Платформы">
      <div className={styles.header}>
        <span className={styles.title}>Платформы</span>
      </div>

      {sources.length === 0 ? (
        <div className={styles.empty}>—</div>
      ) : (
        <div className={styles.cards}>
          {sources.map((source) => {
            const meta = SOURCE_META[source] ?? { label: source, color: '#64748b' }
            const enabled =
              enabledSources === null ? true : enabledSources.has(source)

            const minPrice = minBySource.get(source)
            const count = countBySource.get(source) ?? 0

            const checkboxId = `src_${source.replace(/[^a-zA-Z0-9_-]/g, '_')}`

            return (
              <label
                key={source}
                className={styles.cardLabel}
                style={{ '--src-color': meta.color }}
              >
                <input
                  id={checkboxId}
                  className={styles.checkbox}
                  type="checkbox"
                  checked={enabled}
                  onChange={() => toggleSource(source)}
                />

                <div
                  className={`${styles.card} ${
                    enabled ? styles.cardEnabled : styles.cardDisabled
                  }`}
                >
                  <div className={styles.cardTop}>
                    <span className={styles.platform} title={meta.label}>
                      {meta.label}
                    </span>
                    <span className={styles.countMono}>{count}</span>
                  </div>

                  <div className={styles.priceRow}>
                    <span className={styles.priceLabel}>min</span>
                    <span className={styles.priceValue}>
                      {minPrice != null ? formatPrice(minPrice) : '—'}
                    </span>
                  </div>
                </div>
              </label>
            )
          })}
        </div>
      )}
    </section>
  )
}

