/** Where to go after sign-in: only same-site paths. "//host" and "/\host" are
 * protocol-relative URLs to another site, so they fall back to the home page. */
export function safeRedirect(target: string | undefined): string {
  return target && target.startsWith('/') && !/^\/[/\\]/.test(target) ? target : '/'
}

const PENDING_REDIRECT = 'redirect-after-microsoft'

/** Microsoft sign-in leaves the app and comes back to /auth/callback, so the page to return
 * to (an AI app's consent request, say) is kept in this tab's session storage meanwhile. */
export function rememberRedirect(target: string | undefined): void {
  try {
    sessionStorage.setItem(PENDING_REDIRECT, safeRedirect(target))
  } catch {
    // Storage blocked: sign-in still works and lands on the home page.
  }
}

export function takeRememberedRedirect(): string {
  try {
    const target = sessionStorage.getItem(PENDING_REDIRECT)
    sessionStorage.removeItem(PENDING_REDIRECT)
    return safeRedirect(target ?? undefined)
  } catch {
    return '/'
  }
}
