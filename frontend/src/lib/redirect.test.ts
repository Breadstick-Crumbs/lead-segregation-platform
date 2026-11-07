import { rememberRedirect, safeRedirect, takeRememberedRedirect } from './redirect'

test('only same-site paths are followed after sign-in', () => {
  expect(safeRedirect('/leads/abc?tab=x')).toBe('/leads/abc?tab=x')
  expect(safeRedirect('//evil.example/phish')).toBe('/')
  expect(safeRedirect('/\\evil.example')).toBe('/')
  expect(safeRedirect('https://evil.example')).toBe('/')
  expect(safeRedirect(undefined)).toBe('/')
})

test('the page to return to survives the Microsoft round trip once', () => {
  rememberRedirect('/oauth/consent?request=abc')
  expect(takeRememberedRedirect()).toBe('/oauth/consent?request=abc')
  expect(takeRememberedRedirect()).toBe('/')
  rememberRedirect('//evil.example')
  expect(takeRememberedRedirect()).toBe('/')
})
