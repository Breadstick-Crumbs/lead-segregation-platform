import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import {
  getListMailboxesQueryKey,
  useCreateMailbox,
  useListMailboxes,
  usePollMailboxNow,
  useUpdateMailbox,
  type MailboxRead,
} from '../../api/generated'
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Skeleton,
  TextField,
} from '../../components/ui'
import { timeAgo } from '../../lib/format'

export function MailboxSettings() {
  const mailboxes = useListMailboxes()
  const [adding, setAdding] = useState(false)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-body">
          Company inboxes are checked every minute over IMAP. Replies go out over SMTP from the
          inbox the lead wrote to. Passwords are encrypted and never shown again.
        </p>
        {!adding ? (
          <Button icon="plus" onClick={() => setAdding(true)}>
            Add mailbox
          </Button>
        ) : null}
      </div>
      {adding ? <AddMailbox onDone={() => setAdding(false)} /> : null}
      {mailboxes.isPending ? (
        <Skeleton className="h-32 rounded-card" />
      ) : mailboxes.isError ? (
        <ErrorState error={mailboxes.error} onRetry={() => void mailboxes.refetch()} />
      ) : mailboxes.data.length === 0 ? (
        <EmptyState title="No mailboxes yet" icon="mail">
          Add the inbox your leads write to, for example sales@yourcompany.com. Any provider works
          over IMAP; for Gmail, use an app password.
        </EmptyState>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {mailboxes.data.map((m) => (
            <MailboxCard key={m.id} mailbox={m} />
          ))}
        </ul>
      )}
    </div>
  )
}

function MailboxCard({ mailbox }: { mailbox: MailboxRead }) {
  const queryClient = useQueryClient()
  const refresh = () => void queryClient.invalidateQueries({ queryKey: getListMailboxesQueryKey() })
  const poll = usePollMailboxNow({ mutation: { onSuccess: refresh } })
  const update = useUpdateMailbox({ mutation: { onSuccess: refresh } })
  return (
    <li className="flex flex-col gap-3 rounded-card border border-border bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold text-ink">{mailbox.name}</p>
          <p className="truncate text-sm text-muted">{mailbox.email_address}</p>
        </div>
        {mailbox.is_active ? (
          mailbox.last_error ? (
            <Chip tone="critical">Error</Chip>
          ) : (
            <Chip tone="success">Connected</Chip>
          )
        ) : (
          <Chip>Paused</Chip>
        )}
      </div>
      <p className="font-mono text-xs text-muted">
        IMAP {mailbox.imap_host}:{mailbox.imap_port} · SMTP {mailbox.smtp_host}:{mailbox.smtp_port}
      </p>
      <p className="text-sm text-body">Last checked {timeAgo(mailbox.last_polled_at)}</p>
      {mailbox.last_error ? (
        <p className="text-sm text-priority-critical">{mailbox.last_error}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          icon="refresh"
          busy={poll.isPending}
          disabled={!mailbox.is_active}
          onClick={() => poll.mutate({ mailboxId: mailbox.id })}
        >
          Check now
        </Button>
        <Button
          variant="ghost"
          size="sm"
          busy={update.isPending}
          onClick={() =>
            update.mutate({ mailboxId: mailbox.id, data: { is_active: !mailbox.is_active } })
          }
        >
          {mailbox.is_active ? 'Pause' : 'Resume'}
        </Button>
      </div>
    </li>
  )
}

const EMPTY = {
  name: '',
  email_address: '',
  imap_host: '',
  imap_port: '993',
  smtp_host: '',
  smtp_port: '587',
  username: '',
  password: '',
}

function AddMailbox({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const create = useCreateMailbox({
    mutation: {
      onSuccess: () => void queryClient.invalidateQueries({ queryKey: getListMailboxesQueryKey() }),
    },
  })
  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState<Partial<Record<keyof typeof EMPTY | 'form', string>>>({})
  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value })

  async function submit(event: FormEvent) {
    event.preventDefault()
    const next: typeof errors = {}
    if (!form.name.trim()) next.name = 'Give it a name, like Sales inbox'
    if (!/^\S+@\S+\.\S+$/.test(form.email_address)) next.email_address = 'Enter the inbox address'
    if (!form.imap_host.trim()) next.imap_host = 'For example imap.gmail.com'
    if (!form.smtp_host.trim()) next.smtp_host = 'For example smtp.gmail.com'
    if (!form.password) next.password = 'The mailbox password or app password'
    for (const port of ['imap_port', 'smtp_port'] as const) {
      const n = Number(form[port])
      if (!Number.isInteger(n) || n < 1 || n > 65535) next[port] = '1–65535'
    }
    setErrors(next)
    if (Object.keys(next).length) return
    try {
      await create.mutateAsync({
        data: {
          name: form.name.trim(),
          email_address: form.email_address.trim(),
          imap_host: form.imap_host.trim(),
          imap_port: Number(form.imap_port),
          smtp_host: form.smtp_host.trim(),
          smtp_port: Number(form.smtp_port),
          username: (form.username || form.email_address).trim(),
          password: form.password,
        },
      })
      onDone()
    } catch (error) {
      setErrors({ form: error instanceof Error ? error.message : "Couldn't add the mailbox" })
    }
  }

  return (
    <Card>
      <form noValidate onSubmit={submit} className="flex flex-col gap-4">
        <h2 className="text-lg font-bold text-ink">Add a mailbox</h2>
        {errors.form ? (
          <p role="alert" className="text-sm text-priority-critical">
            {errors.form}
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Name"
            value={form.name}
            onChange={set('name')}
            error={errors.name}
            placeholder="Sales inbox"
          />
          <TextField
            label="Inbox address"
            type="email"
            value={form.email_address}
            onChange={set('email_address')}
            error={errors.email_address}
            placeholder="sales@company.com"
          />
          <TextField
            label="IMAP host"
            value={form.imap_host}
            onChange={set('imap_host')}
            error={errors.imap_host}
          />
          <TextField
            label="IMAP port"
            inputMode="numeric"
            value={form.imap_port}
            onChange={set('imap_port')}
            error={errors.imap_port}
          />
          <TextField
            label="SMTP host"
            value={form.smtp_host}
            onChange={set('smtp_host')}
            error={errors.smtp_host}
          />
          <TextField
            label="SMTP port"
            inputMode="numeric"
            value={form.smtp_port}
            onChange={set('smtp_port')}
            error={errors.smtp_port}
            hint="587 (STARTTLS) or 465 (TLS)"
          />
          <TextField
            label="Username"
            value={form.username}
            onChange={set('username')}
            hint="Leave empty to use the inbox address"
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set('password')}
            error={errors.password}
            hint="Stored encrypted"
          />
        </div>
        <div className="flex gap-2">
          <Button type="submit" busy={create.isPending}>
            Add mailbox
          </Button>
          <Button type="button" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  )
}
