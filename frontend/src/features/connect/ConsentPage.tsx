import { useSearch } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'

import {
  useApproveConsentRequest,
  useDenyConsentRequest,
  useGetConsentRequest,
} from '../../api/generated'
import { Icon } from '../../components/Icon'
import { Button, Eyebrow, Skeleton } from '../../components/ui'
import { ApiError } from '../../lib/api-client'
import { Redirect } from '../auth/Redirect'
import { useSession } from '../auth/useSession'

const CAN = [
  'Search and read leads, including what customers wrote',
  'Change status, assign leads and add notes, as you',
  'Send replies to customers, when you confirm them in the app',
]

/** Where an MCP client (Claude, ChatGPT) sends the browser to ask for access. */
export function ConsentPage() {
  const session = useSession()
  const { request } = useSearch({ strict: false }) as { request?: string }

  if (session.status === 'loading') return <Frame busy />
  if (session.status === 'signed-out' || !session.user) {
    return <Redirect to="sign-in" />
  }
  if (!request)
    return (
      <Frame>
        <Expired />
      </Frame>
    )
  return (
    <Frame>
      <Decision requestId={request} />
    </Frame>
  )
}

function Decision({ requestId }: { requestId: string }) {
  const { user } = useSession()
  const consent = useGetConsentRequest(requestId, { query: { retry: false } })
  const [leaving, setLeaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const go = { onSuccess: (decision: { redirect_url: string }) => leave(decision.redirect_url) }
  const approve = useApproveConsentRequest({ mutation: go })
  const deny = useDenyConsentRequest({ mutation: go })

  function leave(url: string) {
    setLeaving(new URL(url).host)
    window.location.assign(url)
  }

  function decide(kind: 'approve' | 'deny') {
    setError(null)
    const mutation = kind === 'approve' ? approve : deny
    mutation.mutate(
      { requestId },
      {
        onError: (failure) =>
          setError(
            failure instanceof ApiError && failure.status === 404
              ? 'This request has expired. Start again from your AI app.'
              : "Couldn't record your answer. Please try again.",
          ),
      },
    )
  }

  if (consent.isPending) {
    return (
      <div className="flex flex-col gap-3" aria-busy>
        <Skeleton className="h-8 w-3/4" />
        <Skeleton className="h-24" />
      </div>
    )
  }
  if (consent.isError) return <Expired />
  if (leaving) {
    return (
      <p className="font-semibold text-ink" role="status">
        Returning to {leaving}…
      </p>
    )
  }

  const app = consent.data
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl leading-tight font-bold tracking-[-0.01em] text-ink">
          {app.client_name} wants to work your leads
        </h1>
        <p className="mt-2 text-sm text-body">
          Signed in as <span className="font-semibold text-ink">{user?.full_name}</span> (
          {user?.email}). It gets the same access you have, nothing more.
        </p>
      </div>
      <ul className="flex flex-col gap-2.5">
        {CAN.map((line) => (
          <li key={line} className="flex gap-2.5 text-[15px] text-body">
            <span className="mt-0.5 text-accent">
              <Icon name="check" size={18} />
            </span>
            {line}
          </li>
        ))}
      </ul>
      <p className="rounded-control bg-plate px-3 py-2.5 text-sm text-body">
        You'll go back to <span className="font-mono text-ink">{app.redirect_host}</span>. Only
        allow this if you just asked to connect from there.
      </p>
      {error ? (
        <p
          role="alert"
          className="rounded-control bg-priority-critical/10 px-3 py-2 text-sm text-priority-critical"
        >
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          busy={approve.isPending}
          disabled={deny.isPending}
          onClick={() => decide('approve')}
        >
          Allow access
        </Button>
        <Button
          variant="secondary"
          busy={deny.isPending}
          disabled={approve.isPending}
          onClick={() => decide('deny')}
        >
          Cancel
        </Button>
      </div>
      <p className="text-sm text-muted">You can disconnect it any time under AI apps.</p>
    </div>
  )
}

function Expired() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-2xl font-bold text-ink">This request has expired</h1>
      <p className="text-body">
        Sign-in requests last 10 minutes and work once. Start connecting again from your AI app.
      </p>
    </div>
  )
}

function Frame({ children, busy }: { children?: ReactNode; busy?: boolean }) {
  return (
    <main className="grid min-h-screen place-items-center px-4 py-12">
      <div className="w-full max-w-md">
        <Eyebrow>Tawasol · Connect an AI app</Eyebrow>
        <div className="relative mt-4">
          <div aria-hidden className="absolute inset-0 -rotate-2 rounded-plate bg-plate" />
          <div
            className="relative rounded-card border border-border bg-surface p-6"
            aria-busy={busy || undefined}
          >
            {busy ? <p className="text-muted">Loading…</p> : children}
          </div>
        </div>
      </div>
    </main>
  )
}
