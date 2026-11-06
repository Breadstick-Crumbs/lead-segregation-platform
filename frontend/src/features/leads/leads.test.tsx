import { render, screen } from '@testing-library/react'

import { timeAgo } from '../../lib/format'
import { ScoreMeter } from './LeadBits'
import { hasFilters, parseLeadSearch, toListParams } from './search'

test('URL search is validated: unknown values are dropped, not passed to the API', () => {
  const search = parseLeadSearch({
    category: 'demo_request',
    priority: 'urgent', // not a priority
    status: 'new',
    sort: 'random',
    page: '3',
    q: '  ',
    owner: 'me',
  })

  expect(search).toEqual({
    q: undefined,
    source: undefined,
    category: 'demo_request',
    priority: undefined,
    status: 'new',
    owner: 'me',
    sort: undefined,
    page: 3,
  })
  expect(toListParams(search)).toMatchObject({
    category: ['demo_request'],
    status: ['new'],
    owner: 'me',
    sort: 'newest',
    page: 3,
    page_size: 20,
  })
  expect(hasFilters({})).toBe(false)
  expect(hasFilters({ owner: 'me' })).toBe(true)
})

test('the score meter names the priority, not just its colour', () => {
  render(<ScoreMeter score={86} priority="critical" />)

  expect(screen.getByText('86')).toBeInTheDocument()
  expect(screen.getByText('Critical')).toBeInTheDocument()
  expect(screen.getByRole('meter', { name: 'Score 86 of 100, Critical priority' })).toHaveAttribute(
    'aria-valuenow',
    '86',
  )
})

test('an unscored lead says so', () => {
  render(<ScoreMeter score={null} priority={null} />)

  expect(screen.getByText('scoring…')).toBeInTheDocument()
})

test('relative times read naturally', () => {
  const now = new Date('2026-11-06T12:00:00Z')

  expect(timeAgo('2026-11-06T11:59:30Z', now)).toBe('just now')
  expect(timeAgo('2026-11-06T11:48:00Z', now)).toBe('12 min ago')
  expect(timeAgo('2026-11-06T09:00:00Z', now)).toBe('3 h ago')
  expect(timeAgo('2026-11-05T09:00:00Z', now)).toBe('yesterday')
  expect(timeAgo(null, now)).toBe('—')
})
