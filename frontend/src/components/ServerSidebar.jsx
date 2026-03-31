import styles from './ServerSidebar.module.css'

/**
 * Левая панель со списком всех серверов.
 * RAW-строки без какой-либо нормализации — как приходят с /servers.
 *
 * @param {{
 *   servers: string[]
 *   selectedServer: string
 *   onSelect: (server: string) => void
 * }} props
 */
export function ServerSidebar({ servers, selectedServer, onSelect }) {
  return (
    <aside className={styles.sidebar}>
      <div className={styles.heading}>Серверы</div>

      {servers.map((server) => (
        <div
          key={server}
          className={
            server === selectedServer
              ? `${styles.item} ${styles.active}`
              : styles.item
          }
          onClick={() => onSelect(server)}
          title={server}
        >
          {server}
        </div>
      ))}
    </aside>
  )
}
