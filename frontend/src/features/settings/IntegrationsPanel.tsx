import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import {
  getGetIntegrationConfigQueryKey,
  getIntegrationStatusQueryKey,
  useCheckIntegration,
  useGetIntegrationConfig,
  useIntegrationStatus,
  useUpdateIntegrationConfig,
  type ConnectionCheck,
  type IntegrationConfig,
  type IntegrationUpdate,
} from '../../api/generated'
import { Button, Card, Chip, ErrorState, Skeleton, TextField } from '../../components/ui'
import { cn } from '../../lib/cn'

type FieldName = keyof IntegrationUpdate
type CheckName = 'whatsapp' | 'slack' | 'teams' | 'microsoft'

interface FieldSpec {
  name: FieldName
  label: string
  secret?: boolean
  hint?: string
  placeholder?: string
  generate?: boolean // offer a random value (the WhatsApp verify token is ours to choose)
}

interface IntegrationSpec {
  id: CheckName
  title: string
  blurb: string
  fields: FieldSpec[]
  required: FieldName[]
  copy?: (config: IntegrationConfig) => { label: string; value: string }[]
}

const INTEGRATIONS: IntegrationSpec[] = [
  {
    id: 'whatsapp',
    title: 'WhatsApp Cloud API',
    blurb:
      'Receive WhatsApp messages as leads and reply to them. From your Meta app: WhatsApp → API setup.',
    fields: [
      {
        name: 'whatsapp_phone_number_id',
        label: 'Phone number ID',
        hint: 'The numeric ID Meta shows under the number, not the number itself',
        placeholder: '106540352242922',
      },
      { name: 'whatsapp_access_token', label: 'Access token', secret: true },
      {
        name: 'whatsapp_app_secret',
        label: 'App secret',
        secret: true,
        hint: 'App settings → Basic. Used to check that webhooks really come from Meta',
      },
      {
        name: 'whatsapp_verify_token',
        label: 'Webhook verify token',
        secret: true,
        generate: true,
        hint: 'Any value you choose; paste the same one into Meta',
      },
    ],
    required: [
      'whatsapp_phone_number_id',
      'whatsapp_access_token',
      'whatsapp_app_secret',
      'whatsapp_verify_token',
    ],
    copy: (c) => [{ label: 'Callback URL for Meta', value: c.whatsapp_webhook_url }],
  },
  {
    id: 'slack',
    title: 'Slack',
    blurb: 'Escalations and SLA breaches post to a channel through an incoming webhook.',
    fields: [
      {
        name: 'slack_webhook_url',
        label: 'Incoming webhook URL',
        secret: true,
        placeholder: 'https://hooks.slack.com/services/…',
      },
    ],
    required: ['slack_webhook_url'],
  },
  {
    id: 'teams',
    title: 'Microsoft Teams',
    blurb: 'The same alerts in a Teams channel, through a Workflows webhook.',
    fields: [
      {
        name: 'teams_webhook_url',
        label: 'Webhook URL',
        secret: true,
        placeholder: 'https://…',
      },
    ],
    required: ['teams_webhook_url'],
  },
  {
    id: 'microsoft',
    title: 'Sign in with Microsoft',
    blurb:
      'Lets the team sign in with their work accounts. From your Entra app registration: Overview, and Certificates & secrets.',
    fields: [
      {
        name: 'entra_tenant_id',
        label: 'Directory (tenant) ID',
        placeholder: '00000000-0000-0000-0000-000000000000',
      },
      {
        name: 'entra_client_id',
        label: 'Application (client) ID',
        placeholder: '00000000-0000-0000-0000-000000000000',
      },
      { name: 'entra_client_secret', label: 'Client secret value', secret: true },
    ],
    required: ['entra_tenant_id', 'entra_client_id', 'entra_client_secret'],
    copy: (c) => [{ label: 'Redirect URI to register (Web)', value: c.microsoft_redirect_uri }],
  },
]

export function IntegrationsPanel() {
  const config = useGetIntegrationConfig()
  if (config.isPending) return <Skeleton className="h-96 rounded-card" />
  if (config.isError)
    return <ErrorState error={config.error} onRetry={() => void config.refetch()} />
  return (
    <div className="flex flex-col gap-6">
      <ServerOnly />
      {!config.data.secrets_key ? (
        <p
          role="alert"
          className="rounded-control bg-priority-high/10 px-4 py-3 text-sm text-priority-high"
        >
          Secrets can't be saved yet: set SECRETS_ENCRYPTION_KEY on the server first. IDs can still
          be saved.
        </p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {INTEGRATIONS.map((spec) => (
          <IntegrationCard key={spec.id} spec={spec} config={config.data} />
        ))}
      </div>
    </div>
  )
}

/** What stays in the server's environment: shown, not editable here. */
function ServerOnly() {
  const status = useIntegrationStatus()
  if (!status.data) return null
  const s = status.data
  const items = [
    { name: 'OpenAI', ok: s.openai, fix: 'OPENAI_API_KEY' },
    { name: 'Encryption key', ok: s.secrets_key, fix: 'SECRETS_ENCRYPTION_KEY' },
    {
      name: `Mailboxes (${s.active_mailboxes} active)`,
      ok: s.active_mailboxes > 0,
      fix: 'Mailboxes tab',
    },
  ]
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Set on the server">
      {items.map((item) => (
        <li
          key={item.name}
          className="flex items-center gap-2 rounded-full border border-border bg-surface py-1 pr-1 pl-3 text-sm text-ink"
        >
          {item.name}
          {item.ok ? <Chip tone="success">Ready</Chip> : <Chip>Not set · {item.fix}</Chip>}
        </li>
      ))}
    </ul>
  )
}

function IntegrationCard({ spec, config }: { spec: IntegrationSpec; config: IntegrationConfig }) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Partial<Record<FieldName, string>>>({})
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const update = useUpdateIntegrationConfig()
  const check = useCheckIntegration()

  const field = (name: FieldName) => config.fields[name] ?? { value: null, source: null }
  const configured = spec.required.filter((name) => field(name).source !== null).length
  const state = configured === spec.required.length ? 'ready' : configured === 0 ? 'off' : 'partial'

  function saved(next: IntegrationConfig, text: string) {
    queryClient.setQueryData(getGetIntegrationConfigQueryKey(), next)
    void queryClient.invalidateQueries({ queryKey: getIntegrationStatusQueryKey() })
    setMessage({ ok: true, text })
  }

  async function send(data: IntegrationUpdate, text: string) {
    setMessage(null)
    try {
      saved(await update.mutateAsync({ data }), text)
      return true
    } catch (error) {
      setMessage({
        ok: false,
        text: error instanceof Error ? readable(error.message) : "Couldn't save",
      })
      return false
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const data: IntegrationUpdate = {}
    for (const f of spec.fields) {
      const draft = drafts[f.name]?.trim()
      if (draft && draft !== (field(f.name).value ?? '')) data[f.name] = draft
    }
    if (Object.keys(data).length === 0) {
      setMessage({ ok: false, text: 'Nothing changed' })
      return
    }
    if (await send(data, 'Saved. It applies within a few seconds.')) setDrafts({})
  }

  async function test() {
    setMessage(null)
    try {
      const result: ConnectionCheck = await check.mutateAsync({ name: spec.id })
      setMessage({ ok: result.ok, text: result.detail })
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : 'Check failed' })
    }
  }

  return (
    <Card as="article" className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-ink">{spec.title}</h3>
          <p className="mt-1 text-sm text-body">{spec.blurb}</p>
        </div>
        {state === 'ready' ? (
          <Chip tone="success">Set up</Chip>
        ) : state === 'partial' ? (
          <Chip tone="high">Incomplete</Chip>
        ) : (
          <Chip>Not set up</Chip>
        )}
      </header>

      {spec.copy?.(config).map((row) => (
        <CopyRow key={row.label} label={row.label} value={row.value} />
      ))}

      <form noValidate onSubmit={submit} className="flex flex-col gap-4">
        {spec.fields.map((f) => {
          const current = field(f.name)
          const draft = drafts[f.name]
          return (
            <div key={f.name} className="flex flex-col gap-1">
              <TextField
                label={f.label}
                // A token you choose must stay visible, to paste the same one into Meta.
                type={f.secret && !f.generate ? 'password' : 'text'}
                // Not a sign-in form: keep browsers and password managers from filling it.
                autoComplete={f.secret ? 'new-password' : 'off'}
                name={`integration-${f.name}`}
                data-1p-ignore
                data-lpignore="true"
                spellCheck={false}
                value={draft ?? (f.secret ? '' : (current.value ?? ''))}
                onChange={(e) => setDrafts({ ...drafts, [f.name]: e.target.value })}
                placeholder={
                  f.secret && current.source === 'dashboard'
                    ? 'Saved. Leave blank to keep it'
                    : f.secret && current.source === 'environment'
                      ? 'Set on the server. Type to override it here'
                      : f.placeholder
                }
                hint={f.hint}
              />
              <div className="flex flex-wrap items-center gap-x-3 text-[13px] text-muted">
                <span>
                  {current.source === 'dashboard'
                    ? 'Saved here'
                    : current.source === 'environment'
                      ? 'From the server (.env)'
                      : 'Not set'}
                </span>
                {f.generate ? (
                  <button
                    type="button"
                    className="min-h-8 font-semibold text-accent hover:underline"
                    onClick={() => setDrafts({ ...drafts, [f.name]: randomToken() })}
                  >
                    Generate one
                  </button>
                ) : null}
                {current.source === 'dashboard' ? (
                  <button
                    type="button"
                    className="min-h-8 font-semibold text-priority-critical hover:underline"
                    onClick={() => void send({ [f.name]: null }, `${f.label} removed.`)}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            </div>
          )
        })}

        {message ? (
          <p
            role={message.ok ? 'status' : 'alert'}
            className={cn(
              'rounded-control px-3 py-2 text-sm',
              message.ok
                ? 'bg-success/10 text-success'
                : 'bg-priority-critical/10 text-priority-critical',
            )}
          >
            {message.text}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" busy={update.isPending}>
            Save
          </Button>
          <Button
            type="button"
            variant="secondary"
            icon="refresh"
            busy={check.isPending}
            disabled={state === 'off'}
            onClick={() => void test()}
          >
            {spec.id === 'slack' || spec.id === 'teams' ? 'Send a test' : 'Test connection'}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false) // clipboard blocked: the value is still selectable
    }
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-sm font-semibold text-ink">{label}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded-control border border-border bg-bg px-3 py-2.5 font-mono text-[13px] break-all text-ink select-all">
          {value}
        </code>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          icon={copied ? 'check' : 'link'}
          className="shrink-0"
          onClick={() => void copy()}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </div>
  )
}

const LABELS = Object.fromEntries(
  INTEGRATIONS.flatMap((spec) => spec.fields.map((f) => [f.name, f.label])),
)

/** "slack_webhook_url: Value error, Expected …" → "Incoming webhook URL: Expected …" */
function readable(message: string): string {
  return message
    .split('; ')
    .map((part) =>
      part.replace(/^(\w+): (?:Value error, )?/, (_, name: string) => `${LABELS[name] ?? name}: `),
    )
    .join('; ')
}

function randomToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(24))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
