import { useMemo } from 'react'
import styles from './ServerSelect.module.css'

/**
 * Список серверов строится из offers (без отдельного API-запроса).
 *
 * Компонент НЕ знает о роутере: при выборе вызывает onSelectServer,
 * а уже выше (в App/route wrapper) делается navigate('/server/...').
 *
 * @param {{
 *  offers: import('../api/offers').Offer[]
 *  selectedServer: string
 *  onSelectServer: (server: string) => void
 *  disabled?: boolean
 * }} props
 */
export function ServerSelect({
  offers,
  selectedServer,
  onSelectServer,
  disabled = false,
}) {
  const servers = useMemo(() => {
    const set = new Set()
    for (const o of offers) {
      if (o?.server) set.add(o.server)
    }
    return Array.from(set).sort()
  }, [offers])

  return (
    <label className={styles.field}>
      <span className={styles.label}>Сервер</span>
      <select
        className={styles.select}
        value={selectedServer || ''}
        onChange={(e) => onSelectServer(e.target.value)}
        disabled={disabled}
      >
        <option value="">Все</option>
        {servers.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </label>
  )
}

