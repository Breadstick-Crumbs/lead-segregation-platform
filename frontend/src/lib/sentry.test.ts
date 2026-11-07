import { afterEach, expect, test, vi } from 'vitest'

const init = vi.fn()
const captureException = vi.fn()
vi.mock('@sentry/react', () => ({ init, captureException }))

afterEach(() => {
  vi.resetModules()
  vi.unstubAllEnvs()
  init.mockClear()
  captureException.mockClear()
})

test('without a DSN nothing is loaded or reported', async () => {
  vi.stubEnv('VITE_SENTRY_DSN', '')
  const { initSentry, reportError } = await import('./sentry')

  expect(initSentry()).toBeNull()
  reportError(new Error('boom'))
  await Promise.resolve()

  expect(init).not.toHaveBeenCalled()
  expect(captureException).not.toHaveBeenCalled()
})

test('with a DSN errors are reported without personal data', async () => {
  vi.stubEnv('VITE_SENTRY_DSN', 'https://key@o1.ingest.sentry.io/1')
  const { initSentry, reportError } = await import('./sentry')

  await initSentry()
  const error = new Error('boom')
  reportError(error)
  await Promise.resolve()

  expect(init).toHaveBeenCalledWith(
    expect.objectContaining({
      dsn: 'https://key@o1.ingest.sentry.io/1',
      dataCollection: expect.objectContaining({ userInfo: false, httpBodies: [] }),
    }),
  )
  expect(captureException).toHaveBeenCalledWith(error)
})
