import styles from './FiltersBar.module.css'

const FACTIONS = ['Horde', 'Alliance']

/**
 * Панель фильтров. Сервер выбирается через ServerSidebar — здесь только фракция.
 *
 * @param {{
 *   filters: object,
 *   setFilters: function,
 *   disabled: boolean,
 *   priceUnit: 'per_unit'|'per_1k'|'per_1m',
 *   onPriceUnitChange: (unit: 'per_unit'|'per_1k'|'per_1m') => void,
 * }} props
 */
export function FiltersBar({ filters, setFilters, disabled, priceUnit, onPriceUnitChange }) {
  const onFaction = (e) => setFilters({ faction: e.target.value })
  const isRetailLike = /^\([A-Z]{2,}\)\s+(Retail|MoP Classic)\b/.test(String(filters.server || ''))
  const toggleOptions = isRetailLike
    ? [
        { value: 'per_1k', label: '/1K' },
        { value: 'per_1m', label: '/1M' },
      ]
    : [
        { value: 'per_unit', label: '/1' },
        { value: 'per_1k', label: '/1K' },
      ]

  return (
    <div className={styles.bar}>
      <label className={styles.field}>
        <span className={styles.label}>Фракция</span>
        <select
          className={styles.select}
          value={filters.faction}
          onChange={onFaction}
          disabled={disabled}
        >
          <option value="">Все</option>
          {FACTIONS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span className={styles.label}>Цена</span>
        <div className={styles.toggleGroup} role="group" aria-label="Price unit">
          {toggleOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`${styles.toggleBtn} ${priceUnit === option.value ? styles.toggleActive : ''}`}
              onClick={() => onPriceUnitChange(option.value)}
              disabled={disabled || priceUnit === option.value}
            >
              {option.label}
            </button>
          ))}
        </div>
      </label>
    </div>
  )
}
