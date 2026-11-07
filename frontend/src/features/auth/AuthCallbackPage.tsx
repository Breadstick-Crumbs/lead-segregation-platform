import { useNavigate } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'

import { Eyebrow } from '../../components/ui'
import { takeRememberedRedirect } from '../../lib/redirect'
import { useSession } from './useSession'

/** Where Microsoft sign-in lands: the backend has set the refresh cookie; exchange it. */
export function AuthCallbackPage() {
  const { completeExternalSignIn } = useSession()
  const navigate = useNavigate()
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return // StrictMode runs effects twice; refresh tokens are single-use
    started.current = true
    void completeExternalSignIn().then((ok) => {
      const next = takeRememberedRedirect()
      return ok
        ? navigate({ to: next })
        : navigate({ to: '/login', search: { sso_error: 'failed', redirect: next } })
    })
  }, [completeExternalSignIn, navigate])

  return (
    <main className="grid min-h-screen place-items-center">
      <div className="text-center">
        <Eyebrow>Microsoft</Eyebrow>
        <p className="mt-2 text-lg font-semibold text-ink">Signing you in…</p>
      </div>
    </main>
  )
}
