import { act, renderHook } from '@testing-library/react'

import { useScrolled } from './useScrolled'

function scrollTo(y: number) {
  act(() => {
    Object.defineProperty(window, 'scrollY', { value: y, configurable: true })
    window.dispatchEvent(new Event('scroll'))
  })
}

test('the navbar turns solid once the page scrolls past the threshold, and clear again at the top', () => {
  scrollTo(0)
  const { result } = renderHook(() => useScrolled(10))
  expect(result.current).toBe(false)

  scrollTo(10)
  expect(result.current).toBe(false)
  scrollTo(11)
  expect(result.current).toBe(true)
  scrollTo(0)
  expect(result.current).toBe(false)
})
