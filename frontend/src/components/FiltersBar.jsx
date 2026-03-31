import styles from './FiltersBar.module.css'
import { ServerSelect } from './ServerSelect'

const FACTIONS = ['Horde', 'Alliance']

/**
 * @param {{
 *  filters: object,
 *  setFilters: function,
 *  disabled: boolean,
 *  servers: string[],
 *  onSelectServer: (server: string) => void
 * }} props
 */
export function FiltersBar({ filters, setFilters, disabled, servers, onSelectServer }) {
  const onFaction = (e) => setFilters({ faction: e.target.value })

  return (
    <div className={styles.bar}>
      <ServerSelect
        servers={servers}
        selectedServer={filters.server}
        onSelectServer={onSelectServer}
        disabled={disabled}
      />

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
