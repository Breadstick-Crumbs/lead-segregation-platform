// Browser error reporting. Off unless the build sets VITE_SENTRY_DSN: then the SDK is
// downloaded as its own chunk, so a build without a DSN never loads or sends anything.

type SentryModule = typeof import('@sentry/react')

let sentry: Promise<SentryModule> | null = null

export function sentryDsn(): string | undefined {
  const dsn: unknown = import.meta.env.VITE_SENTRY_DSN
  return typeof dsn === 'string' && dsn.trim() ? dsn.trim() : undefined
}

export function initSentry(dsn = sentryDsn()): Promise<SentryModule> | null {
  if (!dsn || sentry) return sentry
  sentry = import('@sentry/react').then((Sentry) => {
    const release: unknown = import.meta.env.VITE_APP_RELEASE
    Sentry.init({
      dsn,
      environment: import.meta.env.MODE,
      release: typeof release === 'string' && release ? release : undefined,
      // No user details, cookies, query strings or request bodies (lead text, passwords).
      dataCollection: { userInfo: false, cookies: false, urlQueryParams: false, httpBodies: [] },
    })
    return Sentry
  })
  return sentry
}

/** Report an error React caught or couldn't handle. A no-op when Sentry is off. */
export function reportError(error: unknown): void {
  void sentry?.then((Sentry) => Sentry.captureException(error))
}
