import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchOffers } from '../api/offers'

/** Авто-загрузка с бэкенда (требование: каждые 15 сек) */
const AUTO_REFRESH_MS = 15_000

/**
 * Центральный хук: офферы, фильтры, загрузка, ошибка, авто- и ручной refresh.
 * Компоненты получают данные только пропсами.
 */
export function useOffers(initialServer = '') {
  const [offers, setOffers] = useState([])
  /** Set<string> | null — null означает, что фильтр по source ещё не инициализирован */
  const [enabledSources, setEnabledSources] = useState(null)
  const [filters, setFiltersRaw] = useState({
    server: initialServer || '',
    faction: '',
    sort_by: 'price',
    limit: 20,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [lastFetched, setLastFetched] = useState(null)
  const [nextRefreshIn, setNextRefreshIn] = useState(
    Math.ceil(AUTO_REFRESH_MS / 1000),
  )

  const filtersRef = useRef(filters)
  const nextRefreshAtRef = useRef(Date.now() + AUTO_REFRESH_MS)
  const autoRefreshTimerRef = useRef(null)
  const countdownTimerRef = useRef(null)

  useEffect(() => {
    filtersRef.current = filters
  }, [filters])

  const load = useCallback(async (currentFilters) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchOffers(currentFilters)
      setOffers(data)
      const sources = Array.from(
        new Set(data.map((o) => o.source).filter(Boolean)),
      )
      // Инициализируем фильтр один раз: при первом успешном ответе.
      setEnabledSources((prev) => (prev !== null ? prev : new Set(sources)))
      setLastFetched(new Date())
    } catch (err) {
      setError(err?.message ?? String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  const restartAutoRefresh = useCallback(() => {
    clearInterval(autoRefreshTimerRef.current)
    clearInterval(countdownTimerRef.current)

    nextRefreshAtRef.current = Date.now() + AUTO_REFRESH_MS
    setNextRefreshIn(
      Math.max(0, Math.ceil((nextRefreshAtRef.current - Date.now()) / 1000)),
    )

    autoRefreshTimerRef.current = setInterval(() => {
      load(filtersRef.current)
      nextRefreshAtRef.current = Date.now() + AUTO_REFRESH_MS
      setNextRefreshIn(Math.ceil(AUTO_REFRESH_MS / 1000))
    }, AUTO_REFRESH_MS)

    countdownTimerRef.current = setInterval(() => {
      const sec = Math.max(
        0,
        Math.ceil((nextRefreshAtRef.current - Date.now()) / 1000),
      )
      setNextRefreshIn(sec)
    }, 1000)
  }, [load])

  const toggleSource = useCallback((source) => {
    setEnabledSources((prev) => {
      if (prev === null) return new Set([source])
      const next = new Set(prev)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })
  }, [])

  const setFilters = useCallback(
    (updater) => {
      setFiltersRaw((prev) => {
        const next =
          typeof updater === 'function' ? updater(prev) : { ...prev, ...updater }
        load(next)
        return next
      })
    },
    [load],
  )

  const refresh = useCallback(() => {
    load(filtersRef.current)
    restartAutoRefresh()
  }, [load, restartAutoRefresh])

  useEffect(() => {
    load(filtersRef.current)
    restartAutoRefresh()
    return () => {
      clearInterval(autoRefreshTimerRef.current)
      clearInterval(countdownTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только mount / unmount
  }, [])

  useEffect(() => {
    setFiltersRaw((prev) => {
      if (prev.server === initialServer) return prev
      const next = { ...prev, server: initialServer }
      filtersRef.current = next
      load(next)
      return next
    })
  }, [initialServer])

  const filteredOffers =
    enabledSources === null
      ? offers
      : offers.filter((o) => enabledSources.has(o.source))

  return {
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
  }
}
