import { describe, expect, it } from 'vitest'

import { _parseGroupLabel } from '../src/components/PriceChart'


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
