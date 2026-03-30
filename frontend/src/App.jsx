import { useRef, useState, useEffect } from 'react'
import { Routes, Route, useNavigate, useParams, Navigate } from 'react-router-dom'
import { useOffers } from './hooks/useOffers'
import { FiltersBar } from './components/FiltersBar'
import { OffersTable } from './components/OffersTable'
import { RefreshButton } from './components/RefreshButton'
import { StatusBar } from './components/StatusBar'
import { StatsBar } from './components/StatsBar'
import { PriceChart } from './components/PriceChart'
import { SourceSummary } from './components/SourceSummary'
import styles from './App.module.css'

const FALLBACK_SERVER = 'firemaw'

// ── Хук: получить первый доступный сервер ────────────────────
// Делает один запрос к /offers, возвращает первый сервер из данных.
// Пока грузится — null (показываем loading), при ошибке — FALLBACK_SERVER.
function useDefaultServer() {
  const [server, setServer] = useState(null) // null = ещё не известен

  useEffect(() => {
    let cancelled = false
    fetch('/api/offers?limit=50&sort_by=price')
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        if (cancelled) return
        const first = data.offers?.[0]?.server
        setServer(first || FALLBACK_SERVER)
      })
      .catch(() => {
        if (!cancelled) setServer(FALLBACK_SERVER)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return server
}

// ── Корневой маршрут: редирект на /server/:default ───────────
function RootRedirect() {
  const defaultServer = useDefaultServer()

  // Пока сервер не известен — ничего не рендерим (мгновенно, один запрос)
  if (defaultServer === null) return null

  return <Navigate to={`/server/${encodeURIComponent(defaultServer)}`} replace />
}

// ── Основной layout ───────────────────────────────────────────
function Dashboard({ initialServer, onSelectServer }) {
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
          offers={offers}
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
      <PriceChart refreshSignal={refreshSignalRef.current} />

      <main className={styles.main}>
        <OffersTable offers={filteredOffers} loading={loading} error={error} />
      </main>
    </div>
  )
}

function DashboardRoute({ initialServer }) {
  const navigate = useNavigate()

  const onSelectServer = (server) => {
    if (!server) navigate('/')
    else navigate(`/server/${encodeURIComponent(server)}`)
  }

  return <Dashboard initialServer={initialServer} onSelectServer={onSelectServer} />
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
