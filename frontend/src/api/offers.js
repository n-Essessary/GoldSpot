const API_BASE = 'https://scintillating-flexibility-production-809a.up.railway.app'

/**
 * @typedef {Object} Offer
 * @property {string}      id
 * @property {string}      source
 * @property {string}      server
 * @property {string}      faction
 * @property {number}      price_per_1k
 * @property {number}      amount_gold
 * @property {string}      seller
 * @property {string|null} offer_url
 * @property {string}      updated_at
 * @property {string|undefined} [fetched_at]
 */

/**
 * @typedef {Object} OffersFilters
 * @property {string} [server]
 * @property {string} [faction]
 * @property {'price'|'amount'} [sort_by]
 * @property {number} [limit]
 */

/**
 * @param {OffersFilters} filters
 * @returns {Promise<Offer[]>}
 */
export async function fetchOffers(filters = {}) {
  const params = new URLSearchParams()

  if (filters.server)  params.set('server',  filters.server)
  if (filters.faction) params.set('faction', filters.faction)
  if (filters.sort_by) params.set('sort_by', filters.sort_by)
  if (filters.limit != null && filters.limit !== '') {
    params.set('limit', String(filters.limit))
  }

  const qs = params.toString()
  const url = `${API_BASE}/offers${qs ? `?${qs}` : ''}`

  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)

  const data = await res.json()
  return data.offers ?? []
}

/**
 * @typedef {Object} PriceHistoryPoint
 * @property {string} timestamp
 * @property {number} avg_price
 * @property {number} min_price
 * @property {number} offer_count
 */

/**
 * @param {{ last?: number }} [opts]
 * @returns {Promise<PriceHistoryPoint[]>}
 */
export async function fetchPriceHistory({ last = 100 } = {}) {
  const url = `${API_BASE}/price-history?last=${last}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  const data = await res.json()
  return data.points ?? []
}
