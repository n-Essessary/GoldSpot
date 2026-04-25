import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('lightweight-charts', () => ({
  ColorType: { Solid: 0 },
  LineStyle: { Dashed: 2, SparseDotted: 1 },
  CrosshairMode: { Normal: 0 },
  createChart: vi.fn(() => ({
    remove: vi.fn(),
    addAreaSeries: vi.fn(() => ({ setData: vi.fn(), update: vi.fn() })),
    addLineSeries: vi.fn(() => ({ setData: vi.fn(), update: vi.fn() })),
    subscribeCrosshairMove: vi.fn(),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(),
      applyOptions: vi.fn(),
      getVisibleLogicalRange: vi.fn(),
      getVisibleRange: vi.fn(),
      setVisibleRange: vi.fn(),
    })),
    applyOptions: vi.fn(),
  })),
}))

import {
  PriceChart,
  _parseGroupLabel,
  applyPriceUnit,
  normalizeFactionForApi,
  fetchLivePrice,
} from '../src/components/PriceChart'
import { API_BASE } from '../src/api/offers'

describe('_parseGroupLabel', () => {
  it('parses US Season of Discovery', () => {
    expect(_parseGroupLabel('(US) Season of Discovery')).toEqual({
      region: 'US',
      version: 'Season of Discovery',
    })
  })

  it('parses AU Season of Discovery', () => {
    expect(_parseGroupLabel('(AU) Season of Discovery')).toEqual({
      region: 'AU',
      version: 'Season of Discovery',
    })
  })

  it('parses EU Classic Era', () => {
    expect(_parseGroupLabel('(EU) Classic Era')).toEqual({
      region: 'EU',
      version: 'Classic Era',
    })
  })

  it('parses EU TBC Classic', () => {
    expect(_parseGroupLabel('(EU) TBC Classic')).toEqual({
      region: 'EU',
      version: 'TBC Classic',
    })
  })
})

describe('applyPriceUnit', () => {
  it('keeps per-1k values for priceUnit="per_1k"', () => {
    expect(applyPriceUnit(12.34, 'per_1k')).toBe(12.34)
  })

  it('divides by 1000 for priceUnit="per_unit"', () => {
    expect(applyPriceUnit(1000, 'per_unit')).toBe(1)
    expect(applyPriceUnit(500, 'per_unit')).toBe(0.5)
  })

  it('multiplies by 1000 for priceUnit="per_1m"', () => {
    expect(applyPriceUnit(12.34, 'per_1m')).toBe(12340)
    expect(applyPriceUnit(0.5, 'per_1m')).toBe(500)
  })
})

describe('normalizeFactionForApi', () => {
  it('maps empty string to All', () => {
    expect(normalizeFactionForApi('')).toBe('All')
  })

  it('keeps Horde and Alliance', () => {
    expect(normalizeFactionForApi('Horde')).toBe('Horde')
    expect(normalizeFactionForApi('Alliance')).toBe('Alliance')
  })

  it('keeps All', () => {
    expect(normalizeFactionForApi('All')).toBe('All')
  })
})

describe('fetchLivePrice', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) })
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns null when response not ok', async () => {
    globalThis.fetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) })
    const r = await fetchLivePrice('A', 'EU', 'Anniversary', 'Horde')
    expect(r).toBeNull()
  })

  it('returns null when no matching entry', async () => {
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entries: [
          {
            server_name: 'Other',
            region: 'EU',
            version: 'Anniversary',
            faction: 'Horde',
            index_price_per_1k: 10,
            min_price: 0.01,
          },
        ],
      }),
    })
    const r = await fetchLivePrice('Firemaw', 'EU', 'Anniversary', 'Horde')
    expect(r).toBeNull()
  })

  it('returns mapped prices for matching entry (min_price per unit → per 1k)', async () => {
    globalThis.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entries: [
          {
            server_name: 'Firemaw',
            region: 'EU',
            version: 'Anniversary',
            faction: 'Horde',
            index_price_per_1k: 12.5,
            min_price: 0.011,
          },
        ],
      }),
    })
    const r = await fetchLivePrice('firemaw', 'eu', 'anniversary', 'Horde')
    expect(r).toEqual({
      index_price_per_1k: 12.5,
      best_ask_alliance_per_1k: null,
      best_ask_horde_per_1k: 11,
      alliance_sources: [],
      horde_sources: [],
    })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      `${API_BASE}/price-index?faction=Horde`
    )
  })

  it('returns null on network error', async () => {
    globalThis.fetch.mockRejectedValueOnce(new Error('network'))
    const r = await fetchLivePrice('A', 'EU', 'X', 'All')
    expect(r).toBeNull()
  })
})

describe('PriceChart — faction & controls', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('does not render faction toggle buttons (synced via FiltersBar)', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ points: [] }) })
    )
    render(
      <PriceChart
        serverSlug="(EU) Anniversary"
        realmName="Firemaw"
        refreshSignal={0}
        faction="Horde"
      />
    )
    expect(screen.queryByRole('button', { name: 'Horde' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Alliance' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'All' })).not.toBeInTheDocument()
  })

  it('still renders period buttons (1H …)', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ points: [] }) })
    )
    render(
      <PriceChart
        serverSlug="(EU) Anniversary"
        realmName=""
        refreshSignal={0}
        faction="All"
      />
    )
    expect(screen.getByRole('button', { name: '1H' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '24H' })).toBeInTheDocument()
  })

  it('passes normalized faction=All to price-history and price-index when faction is empty', async () => {
    const urls = []
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      urls.push(String(url))
      if (String(url).includes('/price-history?') && !String(url).includes('ohlc')) {
        return {
          ok: true,
          json: async () => ({
            // Two samples ≥8h apart so span ≥ 30% of default 24H window (guard keeps per-server path)
            points: [
              {
                recorded_at: '2026-06-01T04:00:00.000Z',
                index_price_per_1k: 10,
                best_ask: 9.5,
              },
              {
                recorded_at: '2026-06-01T12:00:00.000Z',
                index_price_per_1k: 10,
                best_ask: 9.5,
              },
            ],
          }),
        }
      }
      if (String(url).includes('price-index')) {
        return { ok: true, json: async () => ({ entries: [] }) }
      }
      return { ok: false, json: async () => ({}) }
    }))

    render(
      <PriceChart
        serverSlug="(EU) Anniversary"
        realmName="Firemaw"
        refreshSignal={0}
        faction=""
      />
    )

    await waitFor(() => {
      expect(urls.some((u) => u.includes('faction=All'))).toBe(true)
    })
    const historyUrl = urls.find((u) => u.includes('/price-history?') && !u.includes('ohlc'))
    expect(historyUrl).toBeDefined()
    expect(historyUrl).toContain('faction=All')
    await waitFor(() => {
      expect(urls.filter((u) => u.includes('price-index')).length).toBeGreaterThanOrEqual(2)
    })
    expect(urls.some((u) => u.includes('price-index') && u.includes('faction=Alliance'))).toBe(true)
    expect(urls.some((u) => u.includes('price-index') && u.includes('faction=Horde'))).toBe(true)
  })

  it('uses Horde in API query strings when faction prop is Horde', async () => {
    const urls = []
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      urls.push(String(url))
      if (String(url).includes('/price-history?') && !String(url).includes('ohlc')) {
        return {
          ok: true,
          json: async () => ({
            points: [
              {
                recorded_at: '2026-06-01T04:00:00.000Z',
                index_price_per_1k: 10,
                best_ask: 9,
              },
              {
                recorded_at: '2026-06-01T12:00:00.000Z',
                index_price_per_1k: 10,
                best_ask: 9,
              },
            ],
          }),
        }
      }
      if (String(url).includes('price-index')) {
        return { ok: true, json: async () => ({ entries: [] }) }
      }
      return { ok: false, json: async () => ({}) }
    }))

    render(
      <PriceChart
        serverSlug="(EU) Anniversary"
        realmName="Firemaw"
        refreshSignal={0}
        faction="Horde"
      />
    )

    await waitFor(() => {
      expect(urls.some((u) => u.includes('faction=Horde'))).toBe(true)
    })
  })
})
