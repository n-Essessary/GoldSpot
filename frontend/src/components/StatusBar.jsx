import styles from './StatusBar.module.css'

/**
 * @param {{ count: number, lastFetched: Date|null }} props
 */
export function StatusBar({ count, lastFetched }) {
  const timeStr = lastFetched
    ? lastFetched.toLocaleTimeString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '—'

  return (
    <div className={styles.bar}>
      <span className={styles.item}>
        <span className={styles.dot} />
        <span className={styles.badge}>API :8000</span>
      </span>
      <span className={styles.sep}>·</span>
      <span className={styles.item}>
        <span className={styles.label}>в ответе:</span> {count}
      </span>
      <span className={styles.sep}>·</span>
      <span className={styles.item}>
        <span className={styles.label}>загружено:</span> {timeStr}
      </span>
    </div>
  )
}
