import { useEffect, useMemo, useRef, useState } from 'react'
import styles from './ServerSidebar.module.css'

/**
 * Левая панель с двухуровневым деревом серверов.
 *
 * servers        — массив ServerGroup с бэкенда:
 *                  [{ display_server, realms, min_price, game_version? }]
 * selectedServer — display_server выбранной группы: "(EU) Anniversary"
 * selectedRealm  — реалм внутри группы: "Spineshatter" (или "" если не выбран)
 * onSelect(server, realm) — колбэк при выборе
 *
 * @param {{
 *   servers: { display_server: string, realms: string[], min_price: number, game_version?: string }[]
 *   selectedServer: string
 *   selectedRealm?: string
 *   onSelect: (server: string, realm: string) => void
 *   onNavigate?: () => void
 * }} props
 */
export function ServerSidebar({
  servers,
  selectedServer,
  selectedRealm = '',
  onSelect,
  onNavigate,
}) {
  // Авто-раскрываем группу выбранного сервера при инициализации
  const [openGroups, setOpenGroups] = useState(() => {
    const initial = {}
    if (selectedServer) initial[selectedServer] = true
    return initial
  })

  const [openVersions, setOpenVersions] = useState(() => {
    const init = {}
    for (const v of ['MoP Classic', 'Classic Era']) init[v] = true
    return init
  })

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const debounceRef = useRef(null)

  const handleSearchChange = (e) => {
    const v = e.target.value
    setQuery(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null
      setDebouncedQuery(v)
    }, 150)
  }

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    },
    [],
  )

  const toggle = (displayServer) =>
    setOpenGroups((prev) => ({ ...prev, [displayServer]: !prev[displayServer] }))

  const toggleVersion = (v) =>
    setOpenVersions((prev) => ({ ...prev, [v]: !prev[v] }))

  const versionGroups = useMemo(() => {
    const order = ['MoP Classic', 'Classic Era']
    const map = {}
    for (const s of servers) {
      const v = s.game_version || 'Classic Era'
      if (!map[v]) map[v] = []
      map[v].push(s)
    }
    return order.filter((v) => map[v]).map((v) => ({ version: v, groups: map[v] }))
  }, [servers])

  const filteredRows = useMemo(() => {
    const raw = debouncedQuery.trim()
    if (!raw) {
      return servers.map((g) => ({
        group: g,
        hidden: false,
        expanded: null,
        realmsToShow: g.realms,
        highlightRealms: false,
      }))
    }

    const qLower = raw.toLowerCase()

    return servers.map((g) => {
      const groupLabel = stripRegionPrefix(g.display_server)
      const groupMatches = groupLabel.toLowerCase().includes(qLower)
      const matchingRealms = g.realms.filter((r) =>
        r.toLowerCase().includes(qLower),
      )

      if (!groupMatches && matchingRealms.length === 0) {
        return {
          group: g,
          hidden: true,
          expanded: true,
          realmsToShow: [],
          highlightRealms: false,
        }
      }

      if (matchingRealms.length > 0) {
        return {
          group: g,
          hidden: false,
          expanded: true,
          realmsToShow: matchingRealms,
          highlightRealms: true,
        }
      }

      // Group label matches, no realm substring match — show all realms
      return {
        group: g,
        hidden: false,
        expanded: true,
        realmsToShow: g.realms,
        highlightRealms: false,
      }
    })
  }, [servers, debouncedQuery])

  const searchActive = debouncedQuery.trim().length > 0

  return (
    <aside className={styles.sidebar}>
      <input
        type="search"
        className={styles.searchInput}
        placeholder="Поиск сервера..."
        value={query}
        onChange={handleSearchChange}
        autoComplete="off"
        spellCheck={false}
        aria-label="Поиск сервера"
      />

      {versionGroups.map(({ version }) => {
        const isVersionOpen = openVersions[version] ?? true
        const versionFilteredRows = filteredRows.filter(
          (row) => (row.group.game_version || 'Classic Era') === version,
        )
        if (versionFilteredRows.every((r) => r.hidden)) return null
        return (
          <div key={version}>
            <div
              className={styles.heading}
              style={{ cursor: 'pointer', userSelect: 'none' }}
              onClick={() => toggleVersion(version)}
            >
              <span className={styles.arrow}>{isVersionOpen ? '▼' : '▶'}</span>
              {version}
            </div>
            {isVersionOpen &&
              versionFilteredRows.map(
                ({
                  group: { display_server, realms },
                  hidden,
                  expanded,
                  realmsToShow,
                  highlightRealms,
                }) => {
                  if (hidden) return null

                  const isOpen = searchActive ? !!expanded : !!openGroups[display_server]
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
                            if (!searchActive) toggle(display_server)
                          } else {
                            onSelect(display_server, '')
                            onNavigate?.()
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
                          {realmsToShow.map((realm) => {
                            const isActive = isGroupActive && realm === selectedRealm
                            return (
                              <div
                                key={realm}
                                className={[
                                  styles.item,
                                  isActive ? styles.active : '',
                                  highlightRealms ? styles.realmMatch : '',
                                ]
                                  .filter(Boolean)
                                  .join(' ')}
                                onClick={() => {
                                  onSelect(display_server, realm)
                                  onNavigate?.()
                                }}
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
                },
              )}
          </div>
        )
      })}
    </aside>
  )
}

/** Часть display_server без префикса региона: "(EU) Anniversary" → "Anniversary" */
function stripRegionPrefix(displayServer) {
  const m = String(displayServer || '').match(/^\([^)]+\)\s*(.*)$/)
  return m ? m[1].trim() : String(displayServer || '').trim()
}
