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

// ── Хук: загрузить все серверы с /servers ─────────────────────
function useServers() {
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchServers()
      .then((list) => {
        if (!cancelled) setServers(list)
      })
      .catch((err) => {
        console.error('[useServers] failed to load servers:', err)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { servers, loading }
}

// ── Корневой маршрут: редирект на /server/:first ──────────────
function RootRedirect() {
  const { servers, loading } = useServers()

  if (loading) return null

  // servers[0] — ServerGroup, берём display_server
  const first = servers[0]?.display_server ?? ''
  return <Navigate to={`/server/${encodeURIComponent(first)}`} replace />
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

  return (
    <div className={styles.layout}>
      {/* ── Шапка ─────────────────────────────────────────── */}
      <header className={styles.header}>
        <div className={styles.title}>
          <span className={styles.titleAccent}>WoW</span> Gold Market Analytics
        </div>
        <StatusBar count={filteredOffers.length} lastFetched={lastFetched} />
      </header>

      {/* ── Тело: sidebar + основной контент ──────────────── */}
      <div className={styles.body}>
        <ServerSidebar
          servers={servers}
          selectedServer={filters.server}
          selectedRealm={filters.server_name ?? ''}
          onSelect={onSelectServer}
        />

        <div className={styles.content}>
          <div className={styles.toolbar}>
            <FiltersBar
              filters={filters}
              setFilters={setFilters}
              disabled={loading}
            />
          </div>

          <div className={styles.platformBlock}>
            <SourceSummary
              offers={offers}
              enabledSources={enabledSources}
              toggleSource={toggleSource}
            />
          </div>

          <StatsBar offers={filteredOffers} loading={loading} />
          <PriceChart
            refreshSignal={lastFetched}
            serverSlug={filters.server || 'all'}
            factionSlug={filters.faction || 'all'}
          />

          <main className={styles.main}>
            <OffersTable
              offers={filteredOffers}
              loading={loading}
              error={error}
              currentServer={filters.server}
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
      initialRealm={initialRealm}
      servers={servers}
      onSelectServer={onSelectServer}
    />
  )
}

function ServerRoute() {
  const { serverName } = useParams()
  return <DashboardRoute initialServer={decodeURIComponent(serverName)} initialRealm="" />
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
