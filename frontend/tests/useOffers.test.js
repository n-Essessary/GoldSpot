import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useOffers } from '../src/hooks/useOffers'

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
