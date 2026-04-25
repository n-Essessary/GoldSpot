import { useState, useEffect } from 'react'
import { Routes, Route, useNavigate, useParams, Navigate } from 'react-router-dom'
import { useOffers } from './hooks/useOffers'
import { fetchServers } from './api/offers'
import { FiltersBar } from './components/FiltersBar'
import { OffersTable } from './components/OffersTable'
import { ServerSidebar } from './components/ServerSidebar'
import { StatusBar } from './components/StatusBar'
import { StatsBar } from './components/StatsBar'
import { PriceChart } from './components/PriceChart'
import { SourceSummary } from './components/SourceSummary'
import styles from './App.module.css'

const PER_1M_VERSIONS = new Set(['Retail', 'MoP Classic'])

function extractVersion(displayServer) {
  const value = String(displayServer || '').trim()
  if (!value) return ''
  const match = value.match(/^\([^)]+\)\s*(.*)$/)
  return (match ? match[1] : value).trim()
}

// ── Хук: загрузить все серверы с /servers ─────────────────────
function useServers() {
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const MAX_ATTEMPTS = 12 // ~1-2 минуты при задержке 5-10с

  const load = () => {
    setLoading(true)
    fetchServers()
      .then((list) => setServers(Array.isArray(list) ? list : []))
      .catch((err) => {
        console.error('[useServers] failed to load servers:', err)
        setServers([])
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Если backend cache пока пустой — делаем повторные попытки.
  useEffect(() => {
    if (loading) return
    if (servers.length > 0) return
    if (attempt >= MAX_ATTEMPTS) return

    const t = setTimeout(() => {
      setAttempt((a) => a + 1)
      load()
    }, 5000)

    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servers.length, loading, attempt])

  return { servers, loading, attempt, maxAttempts: MAX_ATTEMPTS }
}

// ── Корневой маршрут: редирект на /server/:first ──────────────
function RootRedirect() {
  const { servers, loading } = useServers()

  if (loading) {
    return (
      <div style={{ padding: 24, color: '#e5e7eb', fontFamily: 'system-ui, sans-serif' }}>
        Загрузка серверов…
      </div>
    )
  }

  if (!servers || servers.length === 0) {
    return (
      <div style={{ padding: 24, color: '#e5e7eb', fontFamily: 'system-ui, sans-serif' }}>
        Нет данных о серверах (API /servers пустой).
      </div>
    )
  }

  // servers[0] — ServerGroup
  const firstGroup = servers[0]
  const firstDisplay = firstGroup?.display_server ?? ''
  const firstRealm = firstGroup?.realms?.[0] ?? ''

  // Если у группы есть realms — редиректим сразу на первый realm,
  // иначе редиректим только на группу.
  const to = firstRealm
    ? `/server/${encodeURIComponent(firstDisplay)}/realm/${encodeURIComponent(firstRealm)}`
    : `/server/${encodeURIComponent(firstDisplay)}`

  return <Navigate to={to} replace />
}

// ── Основной layout ───────────────────────────────────────────
function Dashboard({ initialServer, initialRealm, servers, onSelectServer }) {
  const {
    offers,
    filteredOffers,
    enabledSources,
    toggleSource,
    filters,
    setFilters,
    loading,
    error,
    lastFetched,
  } = useOffers(initialServer, initialRealm)

  // Price unit display toggle: canonical API field remains price_per_1k.
  const [priceUnit, setPriceUnit] = useState('per_1k')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const selectedVersion = extractVersion(filters.server)
  const isRetailLike = PER_1M_VERSIONS.has(selectedVersion)
  const availablePriceUnits = isRetailLike ? ['per_1k', 'per_1m'] : ['per_unit', 'per_1k']

  useEffect(() => {
    if (!availablePriceUnits.includes(priceUnit)) {
      setPriceUnit('per_1k')
    }
  }, [availablePriceUnits, priceUnit])

  useEffect(() => {
    if (!sidebarOpen) return

    const onKeyDown = (e) => {
      if (e.key === 'Escape') setSidebarOpen(false)
    }

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = prevOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [sidebarOpen])

  return (
    <div className={styles.layout}>
      {/* ── Шапка ─────────────────────────────────────────── */}
      <header className={styles.header}>
        <button
          className={styles.burgerBtn}
          onClick={() => setSidebarOpen(true)}
          aria-label="Открыть меню серверов"
        >
          ☰
        </button>
        <div className={styles.title}>
          <span className={styles.titleAccent}>WoW</span> Gold Market Analytics
        </div>
        <StatusBar count={filteredOffers.length} lastFetched={lastFetched} />
      </header>

      {/* ── Тело: sidebar + основной контент ──────────────── */}
      <div className={styles.body}>
        <div
          className={`${styles.sidebarOverlay} ${sidebarOpen ? styles.visible : ''}`}
          onClick={() => setSidebarOpen(false)}
        />

        <aside className={`${styles.sidebar} ${sidebarOpen ? styles.open : ''}`}>
          <button
            className={styles.sidebarClose}
            onClick={() => setSidebarOpen(false)}
            aria-label="Закрыть меню серверов"
          >
            ✕
          </button>
          <ServerSidebar
            servers={servers}
            selectedServer={filters.server}
            selectedRealm={filters.server_name ?? ''}
            onSelect={onSelectServer}
            onNavigate={() => setSidebarOpen(false)}
          />
        </aside>

        <div className={styles.content}>
          <div className={styles.toolbar}>
            <FiltersBar
              filters={filters}
              setFilters={setFilters}
              disabled={loading}
              priceUnit={priceUnit}
              onPriceUnitChange={setPriceUnit}
            />
          </div>

          <div className={styles.platformBlock}>
            <SourceSummary
              offers={offers}
              enabledSources={enabledSources}
              toggleSource={toggleSource}
            />
          </div>

          <StatsBar offers={filteredOffers} loading={loading} priceUnit={priceUnit} />
          <PriceChart
            refreshSignal={lastFetched}
            serverSlug={filters.server || 'all'}
            realmName={filters.server_name ?? ''}
            showPer1={priceUnit === 'per_unit'}
            faction={filters.faction || 'All'}
          />

          <main className={styles.main}>
            <OffersTable
              offers={filteredOffers}
              loading={loading}
              error={error}
              currentServer={filters.server}
              priceUnit={priceUnit}
            />
          </main>
        </div>
      </div>
    </div>
  )
}

function DashboardRoute({ initialServer, initialRealm }) {
  const navigate = useNavigate()
  const { servers } = useServers()

  const resolvedInitialRealm = (() => {
    // Backend требует server_name для получения офферов.
    // На маршруте /server/:serverName без /realm мы выбираем первый realm из группы.
    if (initialRealm) return initialRealm
    const group = servers.find((s) => s.display_server === initialServer)
    return group?.realms?.[0] ?? ''
  })()

  // onSelect получает (display_server, realm)
  const onSelectServer = (server, realm) => {
    if (!server) {
      navigate('/')
      return
    }
    const path = realm
      ? `/server/${encodeURIComponent(server)}/realm/${encodeURIComponent(realm)}`
      : `/server/${encodeURIComponent(server)}`
    navigate(path)
  }

  return (
    <Dashboard
      initialServer={initialServer}
      initialRealm={resolvedInitialRealm}
      servers={servers}
      onSelectServer={onSelectServer}
    />
  )
}

function ServerRoute() {
  const { serverName } = useParams()
  const initialServer = decodeURIComponent(serverName)
  return <DashboardRoute initialServer={initialServer} initialRealm="" />
}

function RealmRoute() {
  const { serverName, realmName } = useParams()
  return (
    <DashboardRoute
      initialServer={decodeURIComponent(serverName)}
      initialRealm={decodeURIComponent(realmName)}
    />
  )
}

// ── Роуты ─────────────────────────────────────────────────────
export default function App() {
  return (
    <Routes>
      <Route path="/server/:serverName/realm/:realmName" element={<RealmRoute />} />
      <Route path="/server/:serverName" element={<ServerRoute />} />
      <Route path="/" element={<RootRedirect />} />
    </Routes>
  )
}
