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
  const onLimit = (e) => {
    const raw = e.target.value
    const n = parseInt(raw, 10)
    setFilters({ limit: Number.isFinite(n) ? Math.min(100, Math.max(1, n)) : 20 })
  }

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

      <label className={styles.field}>
        <span className={styles.label}>Limit</span>
        <input
          className={styles.input}
          type="number"
          min={1}
          max={100}
          value={filters.limit}
          onChange={onLimit}
          disabled={disabled}
        />
      </label>
    </div>
  )
}
