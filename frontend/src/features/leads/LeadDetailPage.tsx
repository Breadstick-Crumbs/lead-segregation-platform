import { useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'

import {
  getGetLeadQueryKey,
  useAddLeadNote,
  useGetLead,
  useListAssignableUsers,
  useReplyToLead,
  useSimilarLeads,
  useUpdateLead,
  type EventRead,
  type LeadDetail,
  type LeadStatus,
  type MessageRead,
} from '../../api/generated'
import { Icon } from '../../components/Icon'
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Eyebrow,
  SelectField,
  Skeleton,
  TextAreaField,
} from '../../components/ui'
import { ApiError } from '../../lib/api-client'
import { cn } from '../../lib/cn'
import {
  CATEGORY_LABELS,
  LEAD_STATUSES,
  STATUS_LABELS,
  clockTime,
  dateTime,
  timeAgo,
} from '../../lib/format'
import { useSession } from '../auth/useSession'
import { SourceChip, StatusChip } from './LeadBits'
import { ScoreCard } from './ScoreCard'

function displayName(lead: LeadDetail): string {
  const c = lead.contact
  return c.company_name ?? c.name ?? c.email ?? c.phone_e164 ?? 'Unknown sender'
}

export function LeadDetailPage() {
  const { leadId } = useParams({ from: '/app/leads/$leadId' })
  const lead = useGetLead(leadId)

  if (lead.isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <Skeleton className="h-[480px] rounded-card" />
        <Skeleton className="h-[480px] rounded-plate" />
      </div>
    )
  }
  if (lead.isError) {
    const missing = lead.error instanceof ApiError && lead.error.status === 404
    return missing ? (
      <EmptyState title="This lead doesn't exist" icon="search">
        It may have been removed.{' '}
        <Link to="/" className="font-semibold text-accent">
          Back to leads
        </Link>
      </EmptyState>
    ) : (
      <ErrorState error={lead.error} onRetry={() => void lead.refetch()} />
    )
  }
  return <LeadView lead={lead.data} />
}

function LeadView({ lead }: { lead: LeadDetail }) {
  const c = lead.contact
  return (
    <div className="flex flex-col gap-6">
      <Link
        to="/"
        className="inline-flex w-fit items-center gap-1 text-sm font-semibold text-muted hover:text-ink"
      >
        <Icon name="chevronLeft" size={16} /> All leads
      </Link>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Eyebrow>
            {lead.source === 'email' ? 'Email' : 'WhatsApp'} · received {timeAgo(lead.created_at)}
          </Eyebrow>
          <h1 className="mt-2 text-4xl leading-[1.05] font-bold tracking-[-0.02em] text-ink sm:text-5xl">
            {displayName(lead)}
            <span className="block text-accent">
              {lead.category ? CATEGORY_LABELS[lead.category].toLowerCase() : 'being classified'}
            </span>
          </h1>
          <p className="mt-3 text-body">
            {[c.company_name ? c.name : null, c.email, c.phone_e164].filter(Boolean).join(' · ')}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <SourceChip source={lead.source} />
          <StatusChip status={lead.status} />
          {lead.sla_breached ? <Chip tone="critical">Past SLA</Chip> : null}
        </div>
      </header>

      {lead.possible_duplicate_of ? (
        <p className="flex flex-wrap items-center gap-2 rounded-card border border-border bg-surface px-4 py-3 text-sm">
          <Icon name="link" size={18} />
          Looks like the same enquiry as{' '}
          <Link
            to="/leads/$leadId"
            params={{ leadId: lead.possible_duplicate_of.id }}
            className="font-semibold text-accent hover:underline"
          >
            {lead.possible_duplicate_of.display_name}
          </Link>
          from another address.
        </p>
      ) : null}
      {lead.processing_state === 'failed' ? (
        <p
          role="status"
          className="rounded-card bg-priority-critical/10 px-4 py-3 text-sm text-priority-critical"
        >
          The AI couldn't score this lead: {lead.processing_error ?? 'unknown error'}. A follow-up
          message retries it.
        </p>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex flex-col gap-6">
          <Thread messages={lead.messages} />
          <Composer lead={lead} />
        </div>
        <aside className="flex flex-col gap-6" aria-label="Score and actions">
          <LeadScoreCard lead={lead} />
          <Controls key={`${lead.status}-${lead.owner?.id ?? ''}`} lead={lead} />
          <SimilarLeads leadId={lead.id} />
        </aside>
      </div>
      <Timeline events={lead.events} />
    </div>
  )
}

const DELIVERY: Record<string, string> = {
  queued: 'Sending…',
  sent: 'Sent',
  delivered: 'Delivered',
  read: 'Read',
  failed: 'Not delivered',
}

function Thread({ messages }: { messages: MessageRead[] }) {
  const { user } = useSession()
  const users = useListAssignableUsers()
  const sender = (m: MessageRead) => {
    if (m.is_auto_reply) return 'Auto-reply'
    if (m.sent_by_user_id === user?.id) return 'You'
    return users.data?.find((u) => u.id === m.sent_by_user_id)?.full_name ?? 'A colleague'
  }
  return (
    <Card className="flex flex-col gap-4" as="section">
      <h2 className="text-lg font-bold text-ink">Conversation</h2>
      <ol className="flex flex-col gap-4">
        {messages.map((m) => {
          const outbound = m.direction === 'outbound'
          return (
            <li
              key={m.id}
              className={cn('flex flex-col gap-1', outbound ? 'items-end' : 'items-start')}
            >
              <p className="font-mono text-[11px] tracking-[0.1em] text-muted uppercase">
                {outbound
                  ? `${sender(m)} · ${DELIVERY[m.delivery_status] ?? m.delivery_status} ${clockTime(m.occurred_at)}`
                  : `${m.kind === 'voice' ? 'Voice note · ' : ''}${dateTime(m.occurred_at)}`}
              </p>
              <div
                className={cn(
                  'max-w-[85%] rounded-card px-4 py-3',
                  outbound ? 'bg-accent-tint text-ink' : 'border border-border bg-plate text-ink',
                )}
              >
                {m.subject && !outbound ? (
                  <p dir="auto" className="mb-1 font-semibold">
                    {m.subject}
                  </p>
                ) : null}
                <p dir="auto" className="leading-relaxed whitespace-pre-line">
                  {m.body_text || <span className="text-muted italic">No text</span>}
                </p>
              </div>
              {m.delivery_status === 'failed' && m.delivery_error ? (
                <p className="text-xs text-priority-critical">{m.delivery_error}</p>
              ) : null}
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

function useRefreshLead(leadId: string) {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({ queryKey: getGetLeadQueryKey(leadId) })
    void queryClient.invalidateQueries({
      predicate: (q) =>
        typeof q.queryKey[0] === 'string' && (q.queryKey[0] as string).startsWith('/api/v1/leads'),
    })
  }
}

function Composer({ lead }: { lead: LeadDetail }) {
  const [mode, setMode] = useState<'reply' | 'note'>(lead.can_reply ? 'reply' : 'note')
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)
  const refresh = useRefreshLead(lead.id)
  const reply = useReplyToLead({ mutation: { onSuccess: refresh } })
  const note = useAddLeadNote({ mutation: { onSuccess: refresh } })
  const busy = reply.isPending || note.isPending

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!body.trim()) {
      setError(mode === 'reply' ? 'Write a reply first' : 'Write a note first')
      return
    }
    setError(null)
    try {
      if (mode === 'reply') await reply.mutateAsync({ leadId: lead.id, data: { body } })
      else await note.mutateAsync({ leadId: lead.id, data: { body } })
      setBody('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong; your text is kept.')
    }
  }

  return (
    <Card as="section">
      <div role="group" aria-label="Write" className="mb-4 inline-flex rounded-full bg-plate p-1">
        {(['reply', 'note'] as const).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            className={cn(
              'min-h-11 rounded-full px-4 text-sm font-semibold transition-colors duration-150',
              mode === m ? 'bg-surface text-ink' : 'text-muted hover:text-ink',
            )}
          >
            {m === 'reply'
              ? `Reply by ${lead.source === 'email' ? 'email' : 'WhatsApp'}`
              : 'Internal note'}
          </button>
        ))}
      </div>
      {mode === 'reply' && !lead.can_reply ? (
        <p className="rounded-control bg-plate px-4 py-3 text-sm text-body">
          {lead.reply_blocked_reason}
        </p>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3">
          <TextAreaField
            label={
              mode === 'reply'
                ? `Message to ${lead.contact.name ?? 'the customer'}`
                : 'Note for your team'
            }
            hint={
              mode === 'reply'
                ? 'Sent from the mailbox or number the lead wrote to, in the same thread.'
                : 'Only your team sees notes.'
            }
            dir="auto"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            error={error}
            maxLength={4000}
            rows={4}
          />
          <div className="flex justify-end">
            <Button type="submit" icon={mode === 'reply' ? 'send' : 'note'} busy={busy}>
              {mode === 'reply' ? 'Send reply' : 'Add note'}
            </Button>
          </div>
        </form>
      )}
    </Card>
  )
}

function LeadScoreCard({ lead }: { lead: LeadDetail }) {
  return (
    <ScoreCard
      score={lead.score}
      priority={lead.priority}
      category={lead.category}
      language={lead.language}
      breakdown={lead.latest_score?.breakdown ?? null}
      rationale={lead.latest_score?.rationale ?? null}
      model={lead.latest_score?.model ?? null}
      promptVersion={lead.latest_score?.prompt_version ?? null}
    />
  )
}

function Controls({ lead }: { lead: LeadDetail }) {
  const { user, isAdmin } = useSession()
  const users = useListAssignableUsers()
  const refresh = useRefreshLead(lead.id)
  const update = useUpdateLead({ mutation: { onSuccess: refresh } })
  const [status, setStatus] = useState<LeadStatus>(lead.status)
  const [owner, setOwner] = useState(lead.owner?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const changed = status !== lead.status || (isAdmin && owner !== (lead.owner?.id ?? ''))
  // The current owner may be inactive (not assignable any more): still show who it is.
  const ownerMissing = lead.owner && !(users.data ?? []).some((u) => u.id === lead.owner?.id)

  async function save(data: { status?: LeadStatus; owner_id?: string | null }) {
    setError(null)
    try {
      await update.mutateAsync({ leadId: lead.id, data })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save that change")
    }
  }

  function saveDraft() {
    const data: { status?: LeadStatus; owner_id?: string | null } = {}
    if (status !== lead.status) data.status = status
    if (isAdmin && owner !== (lead.owner?.id ?? '')) data.owner_id = owner || null
    void save(data)
  }

  return (
    <Card as="section" className="flex flex-col gap-4">
      <h2 className="text-lg font-bold text-ink">Handle this lead</h2>
      <SelectField
        label="Status"
        value={status}
        disabled={update.isPending}
        onValueChange={(next) => setStatus(next as LeadStatus)}
        hint={lead.status === 'new' ? 'Moving it on stops the SLA clock.' : undefined}
      >
        {LEAD_STATUSES.map((s) => (
          <option key={s} value={s}>
            {STATUS_LABELS[s]}
          </option>
        ))}
      </SelectField>
      {isAdmin ? (
        <SelectField
          label="Owner"
          value={owner}
          disabled={update.isPending}
          onValueChange={setOwner}
        >
          <option value="">Unassigned</option>
          {ownerMissing && lead.owner ? (
            <option value={lead.owner.id}>{lead.owner.full_name} (inactive)</option>
          ) : null}
          {(users.data ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name}
              {u.role === 'admin' ? ' (admin)' : ''}
            </option>
          ))}
        </SelectField>
      ) : (
        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-semibold text-ink">Owner</p>
          {lead.owner ? (
            <p className="text-body">{lead.owner.id === user?.id ? 'You' : lead.owner.full_name}</p>
          ) : (
            <Button
              variant="secondary"
              icon="user"
              busy={update.isPending}
              onClick={() => void save({ owner_id: user?.id ?? null })}
            >
              Claim this lead
            </Button>
          )}
        </div>
      )}
      {changed ? (
        <div className="flex flex-wrap gap-2">
          <Button busy={update.isPending} onClick={saveDraft}>
            {status === 'spam' || status === 'lost'
              ? `Mark as ${STATUS_LABELS[status].toLowerCase()}`
              : 'Save'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setStatus(lead.status)
              setOwner(lead.owner?.id ?? '')
            }}
          >
            Cancel
          </Button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-priority-critical">
          {error}
        </p>
      ) : null}
    </Card>
  )
}

function SimilarLeads({ leadId }: { leadId: string }) {
  const similar = useSimilarLeads(leadId)
  return (
    <Card as="section" className="flex flex-col gap-3">
      <div>
        <h2 className="text-lg font-bold text-ink">Similar past leads</h2>
        <p className="text-sm text-muted">Found by meaning, not keywords (pgvector).</p>
      </div>
      {similar.isPending ? (
        <Skeleton className="h-20" />
      ) : similar.isError ? (
        <p className="text-sm text-muted">Couldn't load similar leads.</p>
      ) : similar.data.length === 0 ? (
        <p className="text-sm text-muted">Nothing similar yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {similar.data.map((s) => (
            <li key={s.id}>
              <Link
                to="/leads/$leadId"
                params={{ leadId: s.id }}
                className="block rounded-control border border-border px-3 py-2 hover:border-accent"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-semibold text-ink">{s.display_name}</span>
                  <span className="font-mono text-xs text-muted">
                    {Math.round(s.similarity * 100)}% alike
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-sm">
                  <StatusChip status={s.status} />
                  {s.score != null ? (
                    <span className="font-mono text-muted">score {s.score}</span>
                  ) : null}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function eventText(event: EventRead): string {
  const p = event.payload as Record<string, unknown>
  switch (event.type) {
    case 'created':
      return `Lead created from ${String(p.channel ?? 'a message')}`
    case 'message_received':
      return p.kind === 'voice' ? 'Voice note received' : 'Message received'
    case 'classified':
      return `Classified as ${CATEGORY_LABELS[p.category as keyof typeof CATEGORY_LABELS] ?? String(p.category)}`
    case 'scored':
      return p.previous != null
        ? `Re-scored ${String(p.previous)} → ${String(p.score)}`
        : `Scored ${String(p.score)} (${String(p.priority)})`
    case 'assigned':
      return p.rule === 'round_robin'
        ? 'Assigned by round-robin'
        : p.owner_id
          ? 'Reassigned'
          : 'Unassigned'
    case 'status_changed':
      return `Status ${STATUS_LABELS[p.from as LeadStatus] ?? String(p.from)} → ${STATUS_LABELS[p.to as LeadStatus] ?? String(p.to)}`
    case 'auto_replied':
      return p.status === 'failed'
        ? `Auto-reply failed: ${String(p.error ?? '')}`
        : 'Auto-reply sent'
    case 'replied':
      return p.status === 'failed' ? `Reply failed: ${String(p.error ?? '')}` : 'Reply sent'
    case 'notified':
      return String(p.kind).endsWith('_delivery_failed')
        ? `Alert delivery failed (${String(p.channel)})`
        : 'Owner alerted'
    case 'escalated':
      return p.kind === 'sla_breach'
        ? 'Escalated: no response within the SLA'
        : 'Escalated to managers'
    case 'note':
      return String(p.text ?? '')
    case 'processing_failed':
      return `Scoring failed: ${String(p.error ?? '')}`
    default:
      return event.type
  }
}

function Timeline({ events }: { events: EventRead[] }) {
  const ordered = [...events].reverse()
  return (
    <Card as="section">
      <h2 className="mb-4 text-lg font-bold text-ink">History</h2>
      <ol className="relative flex flex-col gap-4 border-l border-border pl-5">
        {ordered.map((event) => (
          <li key={event.id} className="relative">
            <span
              aria-hidden
              className={cn(
                'absolute top-1.5 -left-[25px] size-2.5 rounded-full border-2 border-surface',
                event.type === 'escalated' || event.type === 'processing_failed'
                  ? 'bg-priority-critical'
                  : event.type === 'note'
                    ? 'bg-accent'
                    : 'bg-muted',
              )}
            />
            <p className="font-mono text-[11px] tracking-[0.1em] text-muted uppercase">
              {dateTime(event.created_at)}
              {event.type === 'note' ? ' · note' : ''}
            </p>
            <p
              dir="auto"
              className={cn('text-body', event.type === 'note' && 'whitespace-pre-line text-ink')}
            >
              {eventText(event)}
            </p>
          </li>
        ))}
      </ol>
    </Card>
  )
}
