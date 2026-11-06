/**
 * The fetch every generated API hook goes through.
 *
 * - Sends the in-memory access token as a Bearer header. It is never written to storage; the
 *   refresh token lives in an httpOnly cookie the backend scopes to /api/v1/auth.
 * - On a 401, refreshes once and retries. Concurrent 401s share one refresh ("single
 *   flight"), because refresh tokens rotate: two parallel refreshes would trip the backend's
 *   reuse detection and sign the user out. Across tabs, a Web Lock makes refreshes take
 *   turns, so each one sends the cookie the previous one just rotated.
 * - Non-2xx responses throw `ApiError` with the backend's `{error: {code, message}}`.
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type TokenListener = (token: string | null) => void

let accessToken: string | null = null
let refreshing: Promise<string | null> | null = null
const listeners = new Set<TokenListener>()

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
  for (const listener of listeners) listener(token)
}

export function onAccessTokenChange(listener: TokenListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const REFRESH_LOCK = 'lead-platform-auth-refresh'

function acrossTabs<T>(work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  return locks ? locks.request(REFRESH_LOCK, work) : work()
}

/** Exchange the refresh cookie for a new access token; null when the session is over. */
export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= acrossTabs(async () => {
    try {
      const response = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        credentials: 'same-origin',
      })
      if (!response.ok) {
        setAccessToken(null)
        return null
      }
      const body = (await response.json()) as { access_token: string }
      setAccessToken(body.access_token)
      return body.access_token
    } catch {
      setAccessToken(null)
      return null
    } finally {
      refreshing = null
    }
  })
  return refreshing
}

async function toError(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = response.statusText || `Request failed (${response.status})`
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'error' in body) {
      const error = (body as { error: { code?: string; message?: string } }).error
      code = error.code ?? code
      message = error.message ?? message
    } else if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { msg?: string; loc?: unknown[] }
        const field = first.loc?.slice(1).join('.')
        message = field ? `${field}: ${first.msg ?? 'invalid'}` : (first.msg ?? message)
        code = 'validation_error'
      }
    }
  } catch {
    // not JSON: keep the status text
  }
  return new ApiError(response.status, code, message)
}

function withAuth(options: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return { ...options, headers, credentials: 'same-origin' }
}

export async function apiFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  let response = await fetch(url, withAuth(options, accessToken))
  const isAuthCall = url.startsWith('/api/v1/auth/login') || url.startsWith('/api/v1/auth/refresh')
  if (response.status === 401 && !isAuthCall) {
    const token = await refreshAccessToken()
    if (token) response = await fetch(url, withAuth(options, token))
  }
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}
