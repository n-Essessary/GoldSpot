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

  const first = servers[0] ?? ''
  return <Navigate to={`/server/${encodeURIComponent(first)}`} replace />
}

// ── Основной layout ───────────────────────────────────────────
function Dashboard({ initialServer, servers, onSelectServer }) {
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
  } = useOffers(initialServer)

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
            <OffersTable offers={filteredOffers} loading={loading} error={error} />
          </main>
        </div>
      </div>
    </div>
  )
}

function DashboardRoute({ initialServer }) {
  const navigate = useNavigate()
  const { servers } = useServers()

  const onSelectServer = (server) => {
    if (!server) navigate('/')
    else navigate(`/server/${encodeURIComponent(server)}`)
  }

  return (
    <Dashboard
      initialServer={initialServer}
      servers={servers}
      onSelectServer={onSelectServer}
    />
  )
}

function ServerRoute() {
  const { serverName } = useParams()
  return <DashboardRoute initialServer={serverName} />
}

// ── Роуты ─────────────────────────────────────────────────────
export default function App() {
  return (
    <Routes>
      <Route path="/server/:serverName" element={<ServerRoute />} />
      <Route path="/" element={<RootRedirect />} />
    </Routes>
  )
}
