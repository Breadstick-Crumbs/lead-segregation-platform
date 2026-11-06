import { safeRedirect } from './redirect'

test('only same-site paths are followed after sign-in', () => {
  expect(safeRedirect('/leads/abc?tab=x')).toBe('/leads/abc?tab=x')
  expect(safeRedirect('//evil.example/phish')).toBe('/')
  expect(safeRedirect('/\\evil.example')).toBe('/')
  expect(safeRedirect('https://evil.example')).toBe('/')
  expect(safeRedirect(undefined)).toBe('/')
})
