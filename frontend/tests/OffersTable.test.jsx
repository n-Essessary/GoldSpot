import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, test } from 'vitest'

import { OffersTable, formatPrice, getTop5Set, safeUrl } from '../src/components/OffersTable'


function mkOffer(overrides = {}) {
  return {
    id: '1',
    source: 'g2g',
    server_name: 'Firemaw',
    faction: 'Horde',
    price_per_1k: 3,
    amount_gold: 1000,
    seller: 'seller',
    offer_url: 'https://x',
    updated_at: '2026-01-01T10:00:00Z',
    ...overrides,
  }
}

describe('OffersTable', () => {
  afterEach(() => cleanup())

  it('renders loading spinner when loading and no offers', () => {
    render(<OffersTable offers={[]} loading error={null} />)
    expect(screen.getByText('Загрузка предложений…')).toBeInTheDocument()
  })

  it('renders empty state when no offers and not loading', () => {
    render(<OffersTable offers={[]} loading={false} error={null} />)
    expect(screen.getByText('Предложений не найдено')).toBeInTheDocument()
  })

  it('renders error message', () => {
    render(<OffersTable offers={[]} loading={false} error="Network error" />)
    expect(screen.getByText('Network error')).toBeInTheDocument()
  })

  it('renders correct rows count', () => {
    render(<OffersTable offers={[mkOffer({ id: '1' }), mkOffer({ id: '2' })]} loading={false} error={null} />)
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })

  it('formatPrice handles tiny values', () => {
    expect(formatPrice(0.0009)).toBe('$0.000900')
  })

  it('formatPrice handles normal values', () => {
    expect(formatPrice(1.5)).toBe('$1.50')
  })

  it('first row has crown icon', () => {
    render(<OffersTable offers={[mkOffer({ id: 'a', price_per_1k: 2 }), mkOffer({ id: 'b', price_per_1k: 3 })]} loading={false} error={null} />)
    // Title updated to 'Top Pick' (Task 3 refactor — top-pick display logic)
    expect(screen.getByTitle('Top Pick')).toBeInTheDocument()
  })

  it('buy button links to offer url and renders dash for null', () => {
    render(<OffersTable offers={[mkOffer({ id: 'x', offer_url: 'https://buy' }), mkOffer({ id: 'y', offer_url: null })]} loading={false} error={null} />)
    const links = screen.getAllByRole('link', { name: /Купить у/i })
    expect(links[0]).toHaveAttribute('href', 'https://buy')
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

// ── Bug 3: getTop5Set per-source guarantee ────────────────────────────────────
describe('getTop5Set', () => {
  it('includes cheapest FunPay even when G2G has all lower prices', () => {
    const offers = [
      ...Array(8).fill(null).map((_, i) => ({
        id: `g2g_${i}`,
        source: 'g2g',
        price_per_1k: 10 + i,
        amount_gold: 1000,
        faction: 'Horde',
        seller: 'seller',
        server_name: 'Realm',
        offer_url: null,
        updated_at: null,
      })),
      {
        id: 'fp_1',
        source: 'funpay',
        price_per_1k: 50,
        amount_gold: 1000,
        faction: 'Horde',
        seller: 'seller',
        server_name: 'Realm',
        offer_url: null,
        updated_at: null,
      },
      {
        id: 'fp_2',
        source: 'funpay',
        price_per_1k: 52,
        amount_gold: 1000,
        faction: 'Horde',
        seller: 'seller',
        server_name: 'Realm',
        offer_url: null,
        updated_at: null,
      },
    ]
    const sorted = [...offers].sort((a, b) => a.price_per_1k - b.price_per_1k)
    const top5 = getTop5Set(sorted)
    expect(top5.has('fp_1')).toBe(true)
    expect(top5.has('fp_2')).toBe(true)
  })

  it('always includes cheapest overall (rank 1)', () => {
    const offers = [
      { id: 'a', source: 'g2g', price_per_1k: 5 },
      { id: 'b', source: 'funpay', price_per_1k: 8 },
    ]
    const top5 = getTop5Set(offers)
    expect(top5.has('a')).toBe(true)
  })

  it('returns at most 5 ids', () => {
    const offers = Array(10).fill(null).map((_, i) => ({
      id: `o${i}`,
      source: i % 2 === 0 ? 'g2g' : 'funpay',
      price_per_1k: i + 1,
    }))
    const top5 = getTop5Set(offers)
    expect(top5.size).toBeLessThanOrEqual(5)
  })

  it('handles empty input', () => {
    const top5 = getTop5Set([])
    expect(top5.size).toBe(0)
  })
})

// ── safeUrl — URL sanitization ────────────────────────────────────────────────

describe('safeUrl', () => {
  test('rejects javascript: protocol', () => {
    expect(safeUrl('javascript:alert(1)')).toBeNull()
  })

  test('rejects data: protocol', () => {
    expect(safeUrl('data:text/html,<script>alert(1)</script>')).toBeNull()
  })

  test('accepts https URLs', () => {
    expect(safeUrl('https://www.g2g.com/offer/123')).toBe('https://www.g2g.com/offer/123')
  })

  test('accepts http URLs', () => {
    expect(safeUrl('http://funpay.com/en/chips/114/')).toBe('http://funpay.com/en/chips/114/')
  })

  test('returns null for null input', () => {
    expect(safeUrl(null)).toBeNull()
  })

  test('returns null for empty string', () => {
    expect(safeUrl('')).toBeNull()
  })
})
