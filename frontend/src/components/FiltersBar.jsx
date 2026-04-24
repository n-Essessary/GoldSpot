import styles from './FiltersBar.module.css'

const FACTIONS = ['Horde', 'Alliance']

/**
 * Панель фильтров. Сервер выбирается через ServerSidebar — здесь только фракция.
 *
 * @param {{
 *   filters: object,
 *   setFilters: function,
 *   disabled: boolean,
 * }} props
 */
export function FiltersBar({ filters, setFilters, disabled }) {
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

    </div>
  )
}
