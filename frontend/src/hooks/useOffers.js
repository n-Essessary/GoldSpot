import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchOffers, fetchMeta } from '../api/offers'

// ── Top-pick display helpers ────────────────────────────────────────────────

/**
 * Stable dedup key for an offer.
 * Falls back to a composite key when `o.id` is undefined / empty string,
 * preventing all id-less offers from collapsing into a single bucket.
 *
 * @param {import('../api/offers').Offer} o
 * @returns {string}
 */
export function offerId(o) {
  return o.id || `${o.source}::${o.seller}::${o.price_per_1k}::${o.updated_at}`
}

/**
 * Return a Set of dedup keys for "top pick" offers — cheapest per
 * (source, faction) combination (up to 4: funpay/alliance, funpay/horde,
 * g2g/alliance, g2g/horde).
 *
 * Uses `offerId()` so offers without an `id` field are handled safely.
 *
 * @param {import('../api/offers').Offer[]} offers
 * @returns {Set<string>}
 */
export function getTopPickIds(offers) {
  if (!offers || offers.length === 0) return new Set()
  const topPickMap = {}
  for (const offer of offers) {
    const key = `${(offer.source || '').toLowerCase()}::${(offer.faction || '').toLowerCase()}`
    if (!topPickMap[key] || offer.price_per_1k < topPickMap[key].price_per_1k) {
      topPickMap[key] = offer
    }
  }
  return new Set(Object.values(topPickMap).map(offerId))
}

/**
 * Build the ordered display list for the offers table.
 *
 * Returns `{ sorted, topPickIds }` — a single pass over the offers avoids
 * computing the top-pick set twice in the caller.
 *
 *   sorted:     offers in display order — top picks (up to 4) pinned first,
 *               sorted by price_per_1k ASC within each section.
 *   topPickIds: Set<string> of offerId keys for the top-pick rows.
 *
 * Uses `offerId()` for dedup so offers with missing `id` are safe.
 * Pure function — no side-effects.
 *
 * @param {import('../api/offers').Offer[]} offers
 * @returns {{ sorted: import('../api/offers').Offer[], topPickIds: Set<string> }}
 */
export function buildDisplayList(offers) {
  if (!offers || offers.length === 0) return { sorted: [], topPickIds: new Set() }

  // 1. Find cheapest offer per (source, faction) pair
  const topPickMap = {}
  for (const offer of offers) {
    const key = `${(offer.source || '').toLowerCase()}::${(offer.faction || '').toLowerCase()}`
    if (!topPickMap[key] || offer.price_per_1k < topPickMap[key].price_per_1k) {
      topPickMap[key] = offer
    }
  }

  // 2. Collect top picks and compute stable ID set
  const topPicks   = Object.values(topPickMap)
  const topPickIds = new Set(topPicks.map(offerId))

  // 3. Sort top picks by price_per_1k ASC
  topPicks.sort((a, b) => a.price_per_1k - b.price_per_1k)

  // 4. Remaining offers (not top picks), sorted ASC — use offerId for safe filter
  const remaining = offers.filter((o) => !topPickIds.has(offerId(o)))
  remaining.sort((a, b) => a.price_per_1k - b.price_per_1k)

  // 5. Top picks first, then remaining
  return { sorted: [...topPicks, ...remaining], topPickIds }
}

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
