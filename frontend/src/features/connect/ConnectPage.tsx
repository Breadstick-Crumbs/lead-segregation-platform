import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  getListConnectedAppsQueryKey,
  useDisconnectApp,
  useListConnectedApps,
} from '../../api/generated'
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Eyebrow,
  PageHeader,
  Skeleton,
} from '../../components/ui'
import { dateTime, timeAgo } from '../../lib/format'

const TOOLS = [
  ['Find', 'Search leads, or list the urgent ones waiting on you'],
  ['Read', 'A lead’s conversation, notes, score rationale and similar past leads'],
  ['Act', 'Change status, assign, add notes, and reply once you confirm the text'],
  ['Report', 'Volume, categories, conversion and response times'],
] as const

/** Connecting Claude, ChatGPT or another MCP client, and the apps already connected. */
export function ConnectPage() {
  const serverUrl = `${window.location.origin}/mcp`
  return (
    <div className="flex flex-col gap-8">
      <PageHeader eyebrow="AI apps · MCP" title="Work your leads from" accent="Claude or ChatGPT" />
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <Card className="flex flex-col gap-5">
          <div>
            <h2 className="text-lg font-bold text-ink">Connect an app</h2>
            <p className="mt-1 text-body">
              Add this server as a custom connector in Claude, or an app in ChatGPT’s developer
              mode, then sign in here when it asks. It works as you, with your permissions.
            </p>
          </div>
          <CopyField value={serverUrl} />
          <p className="text-sm text-muted">
            Claude and ChatGPT on the web connect from the internet, so they need the deployed
            address. Claude Desktop and Claude Code can also use a local one.
          </p>
          <dl className="grid gap-3 sm:grid-cols-2">
            {TOOLS.map(([name, what]) => (
              <div key={name} className="rounded-control bg-plate px-3 py-2.5">
                <dt className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
                  {name}
                </dt>
                <dd className="mt-1 text-sm text-body">{what}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <ConnectedApps />
      </div>
    </div>
  )
}

function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false) // clipboard blocked: the URL is still selectable
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <code className="min-w-0 flex-1 rounded-control border border-border bg-bg px-3 py-2.5 font-mono text-sm break-all text-ink select-all">
        {value}
      </code>
      <Button
        variant="secondary"
        size="sm"
        icon={copied ? 'check' : 'link'}
        onClick={() => void copy()}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

function ConnectedApps() {
  const queryClient = useQueryClient()
  const apps = useListConnectedApps()
  const disconnect = useDisconnectApp({
    mutation: {
      onSuccess: () =>
        void queryClient.invalidateQueries({ queryKey: getListConnectedAppsQueryKey() }),
    },
  })

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <Eyebrow>Connected</Eyebrow>
        <h2 className="mt-1 text-lg font-bold text-ink">Your apps</h2>
      </div>
      {apps.isPending ? (
        <Skeleton className="h-20" />
      ) : apps.isError ? (
        <ErrorState error={apps.error} onRetry={() => void apps.refetch()} />
      ) : apps.data.length === 0 ? (
        <EmptyState title="Nothing connected yet" icon="link">
          Apps you allow show here, and you can cut one off at any time.
        </EmptyState>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {apps.data.map((app) => (
            <li key={app.id} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="font-semibold text-ink">{app.client_name}</p>
                <p className="text-sm text-muted">
                  <span className="font-mono">{app.redirect_host}</span> · connected{' '}
                  <span title={dateTime(app.created_at)}>{timeAgo(app.created_at)}</span>
                  {app.last_used_at ? `, last used ${timeAgo(app.last_used_at)}` : ''}
                </p>
              </div>
              <Button
                variant="danger"
                size="sm"
                className="shrink-0"
                busy={disconnect.isPending && disconnect.variables?.grantId === app.id}
                onClick={() => disconnect.mutate({ grantId: app.id })}
              >
                Disconnect
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
