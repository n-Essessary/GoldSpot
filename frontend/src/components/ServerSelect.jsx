import styles from './ServerSelect.module.css'

/**
 * Список серверов приходит напрямую из GET /servers в RAW виде.
 * Никакой нормализации — value опции = RAW строка = то, что принимает бэкенд.
 *
 * Компонент НЕ знает о роутере: при выборе вызывает onSelectServer,
 * а уже выше (в App/route wrapper) делается navigate('/server/...').
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
  // Сортируем по алфавиту, строки не трогаем.
  const sorted = [...servers].sort((a, b) => a.localeCompare(b))

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
        {sorted.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </label>
  )
}
