export const API_BASE = 'https://scintillating-flexibility-production-809a.up.railway.app'

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
 * @typedef {Object} ServerGroup
 * @property {string}   display_server  — читаемое название группы: "(EU) Anniversary"
 * @property {string[]} realms          — реалмы внутри группы (только G2G); [] для FunPay
 * @property {number}   min_price       — минимальная цена $/1k по группе
 */

/**
 * @typedef {Object} OffersFilters
 * @property {string} [server]
 * @property {string} [server_name]
 * @property {string} [faction]
 * @property {'price'|'amount'} [sort_by]
 */

/**
 * @returns {Promise<{ last_update: string | null }>}
 */
export async function fetchMeta() {
  const res = await fetch(`${API_BASE}/meta`)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  return res.json()
}

/**
 * @returns {Promise<ServerGroup[]>}
 */
export async function fetchServers() {
  const res = await fetch(`${API_BASE}/servers`)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  const data = await res.json()
  return data.servers ?? []
}

/**
 * @param {OffersFilters} filters
 * @returns {Promise<Offer[]>}
 */
export async function fetchOffers(filters = {}) {
  const params = new URLSearchParams()

  if (filters.server)      params.set('server',      filters.server)
  if (filters.server_name) params.set('server_name', filters.server_name)
  if (filters.faction)     params.set('faction',     filters.faction)
  if (filters.sort_by)     params.set('sort_by',     filters.sort_by)

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
 * @property {number} price
 * @property {number} min
 * @property {number} max
 * @property {number} count
 */

/**
 * @param {{ last?: number, server?: string, faction?: string }} [opts]
 * @returns {Promise<PriceHistoryPoint[]>}
 */
export async function fetchPriceHistory({ last = 100, server = 'all', faction = 'all' } = {}) {
  const params = new URLSearchParams({ last: String(last), server, faction })
  const url = `${API_BASE}/price-history?${params}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  const data = await res.json()
  return data.points ?? []
}
