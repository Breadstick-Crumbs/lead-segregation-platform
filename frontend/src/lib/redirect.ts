/** Where to go after sign-in: only same-site paths. "//host" and "/\host" are
 * protocol-relative URLs to another site, so they fall back to the home page. */
export function safeRedirect(target: string | undefined): string {
  return target && target.startsWith('/') && !/^\/[/\\]/.test(target) ? target : '/'
}
