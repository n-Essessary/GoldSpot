import styles from './FiltersBar.module.css'

const FACTIONS = ['Horde', 'Alliance']

/**
 * Панель фильтров. Сервер выбирается через ServerSidebar — здесь фракция и Per 1/1K переключатель.
 *
 * @param {{
 *   filters: object,
 *   setFilters: function,
 *   disabled: boolean,
 *   showPer1: boolean,
 *   onTogglePer1: function,
 * }} props
 */
export function FiltersBar({ filters, setFilters, disabled, showPer1 = false, onTogglePer1 }) {
  const onFaction = (e) => setFilters({ faction: e.target.value })

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

      <div className={styles.field}>
        <span className={styles.label}>Цена</span>
        <div className={styles.toggleGroup} role="group" aria-label="Единица цены">
          <button
            type="button"
            className={`${styles.toggleBtn} ${!showPer1 ? styles.toggleActive : ''}`}
            onClick={() => showPer1 && onTogglePer1?.()}
            disabled={disabled}
          >
            Per 1K
          </button>
          <button
            type="button"
            className={`${styles.toggleBtn} ${showPer1 ? styles.toggleActive : ''}`}
            onClick={() => !showPer1 && onTogglePer1?.()}
            disabled={disabled}
          >
            Per 1
          </button>
        </div>
      </div>
    </div>
  )
}
