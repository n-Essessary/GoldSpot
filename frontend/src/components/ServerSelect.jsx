import { useMemo } from 'react'
import styles from './ServerSelect.module.css'
import { normalizeServer } from '../utils/server'

/**
 * Список серверов строится из offers (без отдельного API-запроса).
 *
 * Компонент НЕ знает о роутере: при выборе вызывает onSelectServer,
 * а уже выше (в App/route wrapper) делается navigate('/server/...').
 *
 * value опций — нормализованный slug ("spineshatter"),
 * label опций — красивое имя ("Spineshatter (EU)").
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
  // Map<slug, label> — дедупликация по slug, сортировка по label
  const servers = useMemo(() => {
    const map = new Map()
    for (const o of offers) {
      if (!o?.server) continue
      const { slug, label } = normalizeServer(o.server)
      if (slug && !map.has(slug)) map.set(slug, label)
    }
    return Array.from(map.entries())
      .sort(([, a], [, b]) => a.localeCompare(b))
      .map(([slug, label]) => ({ slug, label }))
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
        {servers.map(({ slug, label }) => (
          <option key={slug} value={slug}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}

