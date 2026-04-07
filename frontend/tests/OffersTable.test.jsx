import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { OffersTable, formatPrice } from '../src/components/OffersTable'


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
    expect(screen.getByTitle('Лучшая цена')).toBeInTheDocument()
  })

  it('buy button links to offer url and renders dash for null', () => {
    render(<OffersTable offers={[mkOffer({ id: 'x', offer_url: 'https://buy' }), mkOffer({ id: 'y', offer_url: null })]} loading={false} error={null} />)
    const links = screen.getAllByRole('link', { name: /Купить у/i })
    expect(links[0]).toHaveAttribute('href', 'https://buy')
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
