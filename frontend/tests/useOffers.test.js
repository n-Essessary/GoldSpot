import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, test, vi } from 'vitest'

import { buildDisplayList, useOffers } from '../src/hooks/useOffers'

vi.mock('../src/api/offers', () => ({
  fetchOffers: vi.fn(),
  fetchMeta: vi.fn(),
}))

import { fetchMeta, fetchOffers } from '../src/api/offers'


describe('useOffers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchOffers.mockResolvedValue([
      { id: '1', source: 'funpay', price_per_1k: 3, amount_gold: 1000, faction: 'Horde' },
      { id: '2', source: 'g2g', price_per_1k: 2, amount_gold: 1000, faction: 'Horde' },
    ])
    fetchMeta.mockResolvedValue({ last_update: '2026-01-01T00:00:00Z' })
  })

  it('initial load calls fetchOffers once on mount', async () => {
    renderHook(() => useOffers('(EU) Anniversary', 'Firemaw'))
    await waitFor(() => expect(fetchOffers).toHaveBeenCalled())
    expect(fetchOffers).toHaveBeenCalledTimes(1)
  })

  it('filter change triggers new fetchOffers call with updated params', async () => {
    const { result } = renderHook(() => useOffers())
    await waitFor(() => expect(fetchOffers).toHaveBeenCalledTimes(1))
    await act(async () => {
      result.current.setFilters({ faction: 'Horde' })
    })
    expect(fetchOffers).toHaveBeenLastCalledWith(expect.objectContaining({ faction: 'Horde' }))
  })

  it('toggleSource("g2g") removes g2g offers from filteredOffers', async () => {
    const { result } = renderHook(() => useOffers())
    await waitFor(() => expect(result.current.filteredOffers.length).toBe(2))
    act(() => result.current.toggleSource('g2g'))
    expect(result.current.filteredOffers.every(o => o.source !== 'g2g')).toBe(true)
  })

  it('silent meta-poll update does not set loading=true', async () => {
    const { result } = renderHook(() => useOffers())
    await waitFor(() => expect(fetchOffers).toHaveBeenCalledTimes(1))
    expect(result.current.loading).toBe(false)
  })

  it('api error sets error state and does not clear existing offers', async () => {
    const { result } = renderHook(() => useOffers())
    await waitFor(() => expect(result.current.offers.length).toBe(2))
    fetchOffers.mockRejectedValueOnce(new Error('boom'))
    await act(async () => {
      result.current.setFilters({ faction: 'Alliance' })
    })
    expect(result.current.error).toBe('boom')
    expect(result.current.offers.length).toBe(2)
  })
})

// ── buildDisplayList — missing id dedup safety ────────────────────────────────

test('buildDisplayList handles offers with missing id', () => {
  const offers = [
    { source: 'funpay', faction: 'Horde',    price_per_1k: 1.5, seller: 'a', updated_at: 't1' },
    { source: 'g2g',    faction: 'Alliance', price_per_1k: 2.0, seller: 'b', updated_at: 't2' },
  ]
  const { sorted } = buildDisplayList(offers)
  expect(sorted).toHaveLength(2)
  expect(sorted[0].price_per_1k).toBe(1.5) // cheaper first (top-pick order)
})

test('buildDisplayList returns empty sorted and empty topPickIds for empty input', () => {
  const { sorted, topPickIds } = buildDisplayList([])
  expect(sorted).toHaveLength(0)
  expect(topPickIds.size).toBe(0)
})

test('buildDisplayList no duplicates between top picks and remaining', () => {
  const offers = [
    { id: 'fp1', source: 'funpay', faction: 'Alliance', price_per_1k: 2.5, seller: 's', updated_at: 't' },
    { id: 'fp2', source: 'funpay', faction: 'Alliance', price_per_1k: 3.0, seller: 's', updated_at: 't' },
    { id: 'g1',  source: 'g2g',    faction: 'Horde',    price_per_1k: 1.8, seller: 's', updated_at: 't' },
    { id: 'g2',  source: 'g2g',    faction: 'Horde',    price_per_1k: 2.1, seller: 's', updated_at: 't' },
  ]
  const { sorted, topPickIds } = buildDisplayList(offers)
  const topSection = sorted.filter((o) => topPickIds.has(o.id))
  const remaining  = sorted.filter((o) => !topPickIds.has(o.id))
  const topIdSet   = new Set(topSection.map((o) => o.id))
  const remIdSet   = new Set(remaining.map((o) => o.id))
  // No offer appears in both sections
  for (const id of topIdSet) expect(remIdSet.has(id)).toBe(false)
  expect(topSection).toHaveLength(2)  // funpay/alliance + g2g/horde
  expect(remaining).toHaveLength(2)   // the two cheaper-priced alternatives
})
