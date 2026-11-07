import { useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

/**
 * Redirects once, on mount. TanStack's `<Navigate>` navigates again whenever its props object
 * changes, which is every render; the router re-renders the page while that navigation is
 * pending, so a signed-out visit looped forever and froze the tab.
 */
export function Redirect({ to }: { to: 'sign-in' | 'home' }) {
  const navigate = useNavigate()
  const href = useLocation().href
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void (to === 'sign-in'
      ? navigate({ to: '/login', search: { redirect: href }, replace: true })
      : navigate({ to: '/', replace: true }))
  }, [to, href, navigate])

  return null
}
