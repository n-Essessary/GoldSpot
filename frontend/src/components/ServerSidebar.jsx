import { useState, useMemo } from 'react'
import styles from './ServerSidebar.module.css'

// ── Группировка ────────────────────────────────────────────────
/**
 * Разбивает flat-список серверов на именованные группы.
 *
 * CASE 1: строка содержит "#"
 *   "(EU) #Anniversary - Годовщина"  → группа "(EU) #Anniversary"
 *   "(EU) #Season of Discovery - Wild Growth" → группа "(EU) #Season of Discovery"
 *   Берём часть ДО последнего " - ".
 *
 * CASE 2: строки без "#"
 *   "(EU) Bloodfang", "(US) Faerlina"  → группа "Classic"
 *
 * Возвращает Map<groupName, string[]> — серверы внутри отсортированы по алфавиту.
 * Порядок групп: сначала "#"-группы (по алфавиту), затем "Classic".
 *
 * @param {string[]} servers
 * @returns {Map<string, string[]>}
 */
function groupServers(servers) {
  const map = new Map()

  for (const server of servers) {
    let group

    if (server.includes('#')) {
      // Часть до последнего " - " — это и есть группа
      const dashIdx = server.lastIndexOf(' - ')
      group = dashIdx !== -1 ? server.slice(0, dashIdx) : server
    } else {
      group = 'Classic'
    }

    if (!map.has(group)) map.set(group, [])
    map.get(group).push(server)
  }

  // Сортируем серверы внутри каждой группы по алфавиту
  for (const list of map.values()) {
    list.sort((a, b) => a.localeCompare(b))
  }

  // Порядок групп: "#"-группы по алфавиту, Classic — в конец
  const sorted = new Map(
    [...map.entries()].sort(([a], [b]) => {
      const aIsClassic = a === 'Classic'
      const bIsClassic = b === 'Classic'
      if (aIsClassic && !bIsClassic) return 1
      if (!aIsClassic && bIsClassic) return -1
      return a.localeCompare(b)
    }),
  )

  return sorted
}

// ── Display helpers ────────────────────────────────────────────

/**
 * Форматирует название группы для отображения в UI.
 * Удаляет символ "#", всё остальное остаётся без изменений.
 *
 * "(EU) #Anniversary"          → "(EU) Anniversary"
 * "(EU) #Season of Discovery"  → "(EU) Season of Discovery"
 * "Classic"                    → "Classic"
 */
function formatGroupName(group) {
  return group.replace(/#/g, '')
}

/**
 * Форматирует название сервера для отображения в UI.
 * Берёт часть после последнего " - ", если оно есть.
 * RAW-значение при этом не меняется — используется только в title/onClick.
 *
 * "(EU) #Anniversary - Soulseeker"  → "Soulseeker"
 * "(EU) Bloodfang"                  → "(EU) Bloodfang"
 */
function formatServerName(server) {
  const dashIdx = server.lastIndexOf(' - ')
  return dashIdx !== -1 ? server.slice(dashIdx + 3) : server
}

// ── Компонент ──────────────────────────────────────────────────
/**
 * Левая панель со сгруппированным списком серверов.
 * RAW-строки без нормализации — как приходят с /servers.
 *
 * @param {{
 *   servers: string[]
 *   selectedServer: string
 *   onSelect: (server: string) => void
 * }} props
 */
export function ServerSidebar({ servers, selectedServer, onSelect }) {
  const grouped = useMemo(() => groupServers(servers), [servers])

  // Авто-раскрываем группу, в которой находится выбранный сервер
  const [openGroups, setOpenGroups] = useState(() => {
    const initial = {}
    for (const [group, list] of groupServers(servers)) {
      if (list.includes(selectedServer)) initial[group] = true
    }
    return initial
  })

  const toggle = (group) =>
    setOpenGroups((prev) => ({ ...prev, [group]: !prev[group] }))

  return (
    <aside className={styles.sidebar}>
      <div className={styles.heading}>Серверы</div>

      {[...grouped.entries()].map(([group, list]) => {
        const isOpen = !!openGroups[group]
        const hasActive = list.includes(selectedServer)

        return (
          <div key={group} className={styles.group}>
            {/* ── Заголовок группы ── */}
            <div
              className={
                hasActive
                  ? `${styles.groupTitle} ${styles.groupTitleActive}`
                  : styles.groupTitle
              }
              onClick={() => toggle(group)}
              title={group}
            >
              <span className={styles.arrow}>{isOpen ? '▼' : '▶'}</span>
              {formatGroupName(group)}
            </div>

            {/* ── Серверы внутри группы ── */}
            {isOpen && (
              <div className={styles.groupItems}>
                {list.map((server) => (
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
                    {formatServerName(server)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </aside>
  )
}
