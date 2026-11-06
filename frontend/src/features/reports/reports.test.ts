import { niceTicks, stackSegments } from './chartMath'
import { duration } from './format'

test('axis ticks end on a clean number at or above the maximum', () => {
  expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8])
  expect(niceTicks(42)).toEqual([0, 20, 40, 60])
  expect(niceTicks(0)).toEqual([0, 1])
  // Regression: small days drew half-lead gridlines.
  expect(niceTicks(1)).toEqual([0, 1])
  expect(niceTicks(3)).toEqual([0, 1, 2, 3])
})

test('stacked segments skip zeros and only the top one is the data end', () => {
  expect(stackSegments({ email: 3, whatsapp: 2 }, ['email', 'whatsapp'])).toEqual([
    { key: 'email', from: 0, to: 3, isTop: false },
    { key: 'whatsapp', from: 3, to: 5, isTop: true },
  ])
  expect(stackSegments({ email: 0, whatsapp: 4 }, ['email', 'whatsapp'])).toEqual([
    { key: 'whatsapp', from: 0, to: 4, isTop: true },
  ])
})

test('durations read in the largest sensible unit', () => {
  expect(duration(35)).toEqual({ value: '35', unit: 'min' })
  expect(duration(200)).toEqual({ value: '3.3', unit: 'h' })
  expect(duration(3 * 1440)).toEqual({ value: '3.0', unit: 'days' })
  expect(duration(null)).toEqual({ value: '—', unit: '' })
})
