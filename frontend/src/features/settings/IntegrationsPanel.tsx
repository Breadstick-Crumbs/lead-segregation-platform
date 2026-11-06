import { useIntegrationStatus } from '../../api/generated'
import { Chip, ErrorState, Skeleton } from '../../components/ui'

export function IntegrationsPanel() {
  const status = useIntegrationStatus()
  if (status.isPending) return <Skeleton className="h-64 rounded-card" />
  if (status.isError)
    return <ErrorState error={status.error} onRetry={() => void status.refetch()} />
  const s = status.data
  const rows: { name: string; ok: boolean; detail: string; fix: string }[] = [
    {
      name: 'OpenAI',
      ok: s.openai,
      detail: 'Classifies and scores leads, transcribes voice notes, embeds for similar leads.',
      fix: 'Set OPENAI_API_KEY.',
    },
    {
      name: 'WhatsApp Cloud API',
      ok: s.whatsapp,
      detail: s.whatsapp_phone_number_id
        ? `Number ID ${s.whatsapp_phone_number_id}`
        : 'Receives and replies to WhatsApp messages.',
      fix: 'Set the WHATSAPP_* values (docs/INGESTION.md).',
    },
    {
      name: 'Mailboxes',
      ok: s.active_mailboxes > 0,
      detail: `${s.active_mailboxes} active`,
      fix: 'Add one under Mailboxes.',
    },
    {
      name: 'Encryption key',
      ok: s.secrets_key,
      detail: 'Encrypts mailbox passwords at rest.',
      fix: 'Set SECRETS_ENCRYPTION_KEY.',
    },
    {
      name: 'Sign in with Microsoft',
      ok: s.microsoft_sign_in,
      detail: 'Entra ID single sign-on.',
      fix: 'Set the ENTRA_* values (docs/MICROSOFT_SIGN_IN.md).',
    },
    {
      name: 'Slack',
      ok: s.slack,
      detail: 'Escalations to a channel.',
      fix: 'Set SLACK_WEBHOOK_URL.',
    },
    {
      name: 'Microsoft Teams',
      ok: s.teams,
      detail: 'Escalations to a channel.',
      fix: 'Set TEAMS_WEBHOOK_URL.',
    },
  ]
  return (
    <ul className="grid gap-3 md:grid-cols-2">
      {rows.map((row) => (
        <li
          key={row.name}
          className="flex items-start justify-between gap-4 rounded-card border border-border bg-surface p-5"
        >
          <div>
            <p className="font-semibold text-ink">{row.name}</p>
            <p className="text-sm text-body">{row.detail}</p>
            {!row.ok ? <p className="mt-1 text-sm text-muted">{row.fix}</p> : null}
          </div>
          {row.ok ? <Chip tone="success">Connected</Chip> : <Chip>Not set up</Chip>}
        </li>
      ))}
    </ul>
  )
}
