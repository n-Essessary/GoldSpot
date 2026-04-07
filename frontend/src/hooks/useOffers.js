import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchOffers, fetchMeta } from '../api/offers'

/** Интервал опроса /meta — лёгкий запрос, не тянет офферы */
const META_POLL_MS = 10_000

/**
 * Центральный хук: офферы, фильтры, загрузка, ошибка.
 *
 * Логика обновления:
 * 1. Первый рендер — сразу грузим офферы.
 * 2. Каждые 10 сек — опрашиваем GET /meta (< 1 КБ).
 *    Если last_update изменился → тихо перезапрашиваем /offers.
 *    State НЕ очищается перед обновлением — UI не мигает.
 * 3. Смена фильтра (сервер / фракция) — немедленный запрос.
 */
export function useOffers(initialServer = '', initialRealm = '') {
  const [offers, setOffers] = useState([])
  /** Set<string> | null — null означает, что фильтр по source ещё не инициализирован */
  const [enabledSources, setEnabledSources] = useState(null)
  const [filters, setFiltersRaw] = useState({
    server: initialServer || '',
    server_name: initialRealm || '',
    faction: '',
    sort_by: 'price',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // TODO(I4): expose backend /meta last_update as dataVersion in UI.
  const [lastFetched, setLastFetched] = useState(null)

  const filtersRef = useRef(filters)
  // Последняя известная версия данных с бэкенда
  const lastUpdateRef = useRef(null)

  useEffect(() => {
    filtersRef.current = filters
  }, [filters])

  // ── Загрузка офферов ────────────────────────────────────────
  // silent=true — не трогает loading/error (фоновое обновление без мигания)
  const load = useCallback(async (currentFilters, silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const data = await fetchOffers(currentFilters)
      // Обновляем state без предварительного сброса — данные "плавно" меняются
      setOffers(data)
      const sources = Array.from(
        new Set(data.map((o) => o.source).filter(Boolean)),
      )
      setEnabledSources((prev) => (prev !== null ? prev : new Set(sources)))
      setLastFetched(new Date())
      // Sync local data version after successful offers load (no extra request on mount path).
      fetchMeta()
        .then((meta) => {
          if (meta?.last_update) lastUpdateRef.current = meta.last_update
        })
        .catch(() => {})
    } catch (err) {
      if (!silent) setError(err?.message ?? String(err))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  // ── Meta-polling: опрашиваем только версию ──────────────────
  // Реальный fetch офферов — только если версия изменилась
  useEffect(() => {
    const poll = async () => {
      try {
        const meta = await fetchMeta()
        const incoming = meta.last_update
        if (incoming && incoming !== lastUpdateRef.current) {
          lastUpdateRef.current = incoming
          load(filtersRef.current, /* silent */ true)
        }
      } catch {
        // Сеть недоступна — молчим, не ломаем UI
      }
    }

    // Первичная загрузка сразу при монтировании
    load(filtersRef.current)

    const timer = setInterval(poll, META_POLL_MS)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только mount
  }, [])

  // ── Реакция на смену маршрута (initialServer / initialRealm) ──
  useEffect(() => {
    setFiltersRaw((prev) => {
      if (prev.server === initialServer && prev.server_name === (initialRealm || '')) return prev
      const next = { ...prev, server: initialServer, server_name: initialRealm || '' }
      filtersRef.current = next
      load(next)
      return next
    })
  }, [initialServer, initialRealm, load])

  // ── Смена фильтра пользователем ─────────────────────────────
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

  const toggleSource = useCallback((source) => {
    setEnabledSources((prev) => {
      if (prev === null) return new Set([source])
      const next = new Set(prev)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })
  }, [])

  // Клиентская фильтрация: только по source (сервер фильтрует бэкенд)
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
  }
}
