import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'

import { getMicrosoftLoginUrl, useAuthProviders } from '../../api/generated'
import { Button, Eyebrow, TextField } from '../../components/ui'
import { ApiError } from '../../lib/api-client'
import { rememberRedirect, safeRedirect } from '../../lib/redirect'
import { useSession } from './useSession'

const SSO_ERRORS: Record<string, string> = {
  cancelled: 'Microsoft sign-in was cancelled.',
  not_invited: 'Your Microsoft account has no access yet. Ask an admin to add you.',
  inactive: 'Your account is deactivated. Ask an admin to reactivate it.',
  link_mismatch: 'That Microsoft account is linked to a different user.',
  failed: "Microsoft sign-in didn't complete. Please try again.",
}

export function LoginPage() {
  const { signIn } = useSession()
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { sso_error?: string; redirect?: string }
  const providers = useAuthProviders()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({})
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const next: typeof errors = {}
    if (!/^\S+@\S+\.\S+$/.test(email.trim()))
      next.email = 'Enter your work email, like name@company.com'
    if (!password) next.password = 'Enter your password'
    setErrors(next)
    if (next.email || next.password) return
    setBusy(true)
    try {
      await signIn(email.trim(), password)
      await navigate({ to: safeRedirect(search.redirect) })
    } catch (error) {
      setErrors({
        form:
          error instanceof ApiError && error.status === 401
            ? "That email and password don't match. Check them and try again."
            : error instanceof ApiError && error.status === 429
              ? 'Too many attempts. Wait a minute and try again.'
              : "Couldn't sign in right now. Please try again.",
      })
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  const ssoError = search.sso_error ? (SSO_ERRORS[search.sso_error] ?? SSO_ERRORS.failed) : null

  return (
    <main className="grid min-h-screen place-items-center px-4 py-12">
      <div className="w-full max-w-md">
        <Eyebrow>Tawasol · Lead platform</Eyebrow>
        <h1 className="mt-3 text-5xl leading-[1.02] font-bold tracking-[-0.02em] text-ink">
          Every lead,
          <span className="block text-accent">in its place.</span>
        </h1>
        <p className="mt-4 text-body">
          Email and WhatsApp enquiries, sorted and scored, with the ones that need you first.
        </p>

        <div className="relative mt-8">
          <div aria-hidden className="absolute inset-0 -rotate-2 rounded-plate bg-plate" />
          <form
            noValidate
            onSubmit={submit}
            className="relative flex flex-col gap-4 rounded-card border border-border bg-surface p-6"
          >
            {ssoError || errors.form ? (
              <p
                role="alert"
                className="rounded-control bg-priority-critical/10 px-3 py-2 text-sm text-priority-critical"
              >
                {errors.form ?? ssoError}
              </p>
            ) : null}
            <TextField
              label="Work email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              error={errors.email}
              autoFocus
            />
            <TextField
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              error={errors.password}
            />
            <Button type="submit" busy={busy}>
              Sign in
            </Button>
            {providers.data?.microsoft ? (
              <>
                <div className="flex items-center gap-3 text-xs text-muted">
                  <span className="h-px flex-1 bg-border" />
                  or
                  <span className="h-px flex-1 bg-border" />
                </div>
                <a
                  href={getMicrosoftLoginUrl()}
                  onClick={() => rememberRedirect(search.redirect)}
                  className="inline-flex min-h-11 items-center justify-center gap-3 rounded-control border border-border bg-surface px-4 font-semibold text-ink transition-colors duration-150 hover:bg-plate"
                >
                  <MicrosoftMark />
                  Sign in with Microsoft
                </a>
              </>
            ) : null}
          </form>
        </div>
      </div>
    </main>
  )
}

function MicrosoftMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden>
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}
