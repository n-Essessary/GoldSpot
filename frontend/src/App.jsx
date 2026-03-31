import { useRef, useState, useEffect } from 'react'
import { Routes, Route, useNavigate, useParams, Navigate } from 'react-router-dom'
import { useOffers } from './hooks/useOffers'
import { fetchServers } from './api/offers'
import { FiltersBar } from './components/FiltersBar'
import { OffersTable } from './components/OffersTable'
import { RefreshButton } from './components/RefreshButton'
import { StatusBar } from './components/StatusBar'
import { StatsBar } from './components/StatsBar'
import { PriceChart } from './components/PriceChart'
import { SourceSummary } from './components/SourceSummary'
import styles from './App.module.css'

// ── Хук: загрузить все серверы с /servers ─────────────────────
// Возвращает { servers: string[], loading: boolean }
// servers — массив всех серверов (80+) с бэкенда, без ограничений.
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
// Использует первый сервер из /servers вместо запроса к /offers.
function RootRedirect() {
  const { servers, loading } = useServers()

  if (loading) return null

  // Используем RAW строку из /servers — никакой нормализации.
  // encodeURIComponent корректно обработает "(EU) Flamegor" → "%28EU%29%20Flamegor",
  // React Router при useParams() автоматически декодирует обратно в "(EU) Flamegor".
  const first = servers[0] ?? ''

  return <Navigate to={`/server/${encodeURIComponent(first)}`} replace />
}

// ── Основной layout ───────────────────────────────────────────
function Dashboard({ initialServer, servers, onSelectServer }) {
  const refreshSignalRef = useRef(0)
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
    refresh,
    nextRefreshIn,
  } = useOffers(initialServer)

  function handleRefresh() {
    refreshSignalRef.current += 1
    refresh()
  }

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.title}>
          <span className={styles.titleAccent}>WoW</span> Gold Market Analytics
        </div>
        <StatusBar count={filteredOffers.length} lastFetched={lastFetched} />
      </header>

      <div className={styles.toolbar}>
        <FiltersBar
          filters={filters}
          setFilters={setFilters}
          disabled={loading}
          servers={servers}
          onSelectServer={onSelectServer}
        />
        <RefreshButton
          onRefresh={handleRefresh}
          loading={loading}
          nextRefreshIn={nextRefreshIn}
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
  )
}

function DashboardRoute({ initialServer }) {
  const navigate = useNavigate()
  // Загружаем серверы один раз на уровне DashboardRoute,
  // чтобы список не перегружался при смене сервера.
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
