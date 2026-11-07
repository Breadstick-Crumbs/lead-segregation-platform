import { useState } from 'react'

import { useListMailboxes } from '../../api/generated'
import { Card, ErrorState, SelectField, Skeleton, TextAreaField } from '../../components/ui'
import { SaveBar } from './SaveBar'
import { useSettingsDoc } from './useSettingsDoc'

interface Alerts {
  escalation_emails: string[]
  email_alerts: boolean
  alert_mailbox_id: string | null
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function AlertSettings() {
  const doc = useSettingsDoc<Alerts>('notifications')
  if (doc.error) return <ErrorState error={doc.error} onRetry={doc.retry} />
  if (!doc.value) return <Skeleton className="h-80 rounded-card" />
  return <AlertForm doc={doc} saved={doc.value} />
}

function AlertForm({
  doc,
  saved,
}: {
  doc: ReturnType<typeof useSettingsDoc<Alerts>>
  saved: Alerts
}) {
  const mailboxes = useListMailboxes()
  const [draft, setDraft] = useState<Alerts>(saved)
  const [emailsText, setEmailsText] = useState(saved.escalation_emails.join('\n'))

  const emails = emailsText
    .split(/[\s,;]+/)
    .map((e) => e.trim())
    .filter(Boolean)
  const bad = emails.filter((e) => !EMAIL.test(e))
  const next: Alerts = { ...draft, escalation_emails: emails }
  const dirty = JSON.stringify(next) !== JSON.stringify(saved)

  return (
    <Card className="flex max-w-3xl flex-col gap-5">
      <div>
        <h2 className="text-lg font-bold text-ink">Alerts and escalation</h2>
        <p className="text-sm text-muted">
          High leads alert their owner. Critical leads, and high or critical leads nobody answers
          within the SLA, also go to the people below and to Slack or Teams when configured.
        </p>
      </div>
      <TextAreaField
        label="Escalation list"
        hint="Sales managers, one email per line. They don't need an account."
        rows={4}
        value={emailsText}
        onChange={(e) => setEmailsText(e.target.value)}
        error={bad.length ? `Not an email address: ${bad.join(', ')}` : null}
      />
      <label className="flex min-h-11 items-center gap-3">
        <input
          type="checkbox"
          checked={draft.email_alerts}
          onChange={(e) => setDraft({ ...draft, email_alerts: e.target.checked })}
          className="size-5 accent-[var(--color-accent)]"
        />
        <span className="text-ink">Send alerts by email too (in-app alerts are always on)</span>
      </label>
      <SelectField
        label="Send alert emails from"
        value={draft.alert_mailbox_id ?? ''}
        onValueChange={(next) => setDraft({ ...draft, alert_mailbox_id: next || null })}
        hint={mailboxes.data?.length ? undefined : 'Add a mailbox first, under Mailboxes.'}
      >
        <option value="">The first active mailbox</option>
        {(mailboxes.data ?? []).map((m) => (
          <option key={m.id} value={m.id}>
            {m.name} ({m.email_address})
          </option>
        ))}
      </SelectField>
      <SaveBar
        dirty={dirty && bad.length === 0}
        saving={doc.saving}
        onSave={() => doc.save(next)}
        onReset={() => {
          setDraft(saved)
          setEmailsText(saved.escalation_emails.join('\n'))
        }}
      />
    </Card>
  )
}
