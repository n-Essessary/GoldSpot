import { useMemo } from 'react'
import styles from './ServerSelect.module.css'
import { normalizeServer } from '../utils/server'

/**
 * Список серверов приходит напрямую из GET /servers — без ограничений.
 *
 * Компонент НЕ знает о роутере: при выборе вызывает onSelectServer,
 * а уже выше (в App/route wrapper) делается navigate('/server/...').
 *
 * value опций — нормализованный slug ("spineshatter"),
 * label опций — красивое имя ("Spineshatter (EU)").
 *
 * @param {{
 *  servers: string[]
 *  selectedServer: string
 *  onSelectServer: (server: string) => void
 *  disabled?: boolean
 * }} props
 */
export function ServerSelect({
  servers,
  selectedServer,
  onSelectServer,
  disabled = false,
}) {
  // Нормализуем raw-строки из /servers → { slug, label }, сортируем по label.
  // Без фильтрации и ограничения количества.
  const options = useMemo(
    () =>
      servers
        .map((raw) => normalizeServer(raw))
        .filter(({ slug }) => slug)
        .sort((a, b) => a.label.localeCompare(b.label)),
    [servers],
  )

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
        {options.map(({ slug, label }) => (
          <option key={slug} value={slug}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}
