import { useState } from 'react'
import styles from './ServerSidebar.module.css'

/**
 * Левая панель с двухуровневым деревом серверов.
 *
 * servers        — массив ServerGroup с бэкенда:
 *                  [{ display_server: "(EU) Anniversary", realms: ["Firemaw", "Spineshatter"], min_price: 0.42 }]
 * selectedServer — display_server выбранной группы: "(EU) Anniversary"
 * selectedRealm  — реалм внутри группы: "Spineshatter" (или "" если не выбран)
 * onSelect(server, realm) — колбэк при выборе
 *
 * @param {{
 *   servers: { display_server: string, realms: string[], min_price: number }[]
 *   selectedServer: string
 *   selectedRealm?: string
 *   onSelect: (server: string, realm: string) => void
 * }} props
 */
export function ServerSidebar({ servers, selectedServer, selectedRealm = '', onSelect }) {
  // Авто-раскрываем группу выбранного сервера при инициализации
  const [openGroups, setOpenGroups] = useState(() => {
    const initial = {}
    if (selectedServer) initial[selectedServer] = true
    return initial
  })

  const toggle = (displayServer) =>
    setOpenGroups((prev) => ({ ...prev, [displayServer]: !prev[displayServer] }))

  return (
    <aside className={styles.sidebar}>
      <div className={styles.heading}>Серверы</div>

      {servers.map(({ display_server, realms }) => {
        const isOpen = !!openGroups[display_server]
        const isGroupActive = display_server === selectedServer
        const hasRealms = realms.length > 0

        return (
          <div key={display_server} className={styles.group}>
            {/* ── Заголовок группы ── */}
            <div
              className={
                isGroupActive
                  ? `${styles.groupTitle} ${styles.groupTitleActive}`
                  : styles.groupTitle
              }
              onClick={() => {
                if (hasRealms) {
                  // Раскрываем/скрываем список реалмов
                  toggle(display_server)
                } else {
                  // У группы нет реалмов (FunPay) — выбираем сразу
                  onSelect(display_server, '')
                }
              }}
              title={display_server}
            >
              <span className={styles.arrow}>
                {hasRealms ? (isOpen ? '▼' : '▶') : '·'}
              </span>
              {display_server}
            </div>

            {/* ── Реалмы внутри группы ── */}
            {isOpen && hasRealms && (
              <div className={styles.groupItems}>
                {realms.map((realm) => {
                  const isActive = isGroupActive && realm === selectedRealm
                  return (
                    <div
                      key={realm}
                      className={isActive ? `${styles.item} ${styles.active}` : styles.item}
                      onClick={() => onSelect(display_server, realm)}
                      title={realm}
                    >
                      {realm}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </aside>
  )
}
