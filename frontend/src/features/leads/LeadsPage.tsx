import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useId, useState } from 'react'

import {
  useLeadStats,
  useListAssignableUsers,
  useListLeads,
  type LeadListItem,
  type LeadStats,
} from '../../api/generated'
import { Icon } from '../../components/Icon'
import { Select } from '../../components/Select'
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Eyebrow,
  PageHeader,
  ROW,
  ROW_LINK,
  Skeleton,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  CATEGORY_LABELS,
  LEAD_CATEGORIES,
  LEAD_STATUSES,
  PRIORITIES,
  PRIORITY_LABELS,
  STATUS_LABELS,
  percent,
  timeAgo,
} from '../../lib/format'
import { useSession } from '../auth/useSession'
import { CategoryChip, ScoreMeter, SourceChip, StatusChip } from './LeadBits'
import { PAGE_SIZE, hasFilters, toListParams, type LeadSearch } from './search'

function leadName(lead: LeadListItem): string {
  const c = lead.contact
  return c.company_name ?? c.name ?? c.email ?? c.phone_e164 ?? 'Unknown sender'
}

function personLine(lead: LeadListItem): string | null {
  const c = lead.contact
  const person = c.company_name ? c.name : null
  return [person, c.email ?? c.phone_e164].filter(Boolean).join(' · ') || null
}

export function LeadsPage() {
  return (
    <div className="flex flex-col gap-8">
      <PageHeader eyebrow="Pipeline" title="Leads" accent="that need you" />
      <StatsRow />
      <NeedsYou />
      <LeadTable />
    </div>
  )
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: string
  unit?: string
  tone?: string
}) {
  return (
    <div className="rounded-card bg-plate px-5 py-4">
      <Eyebrow>{label}</Eyebrow>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span
          className={cn(
            'text-5xl leading-none font-bold tracking-[-0.02em] tabular-nums',
            tone ?? 'text-ink',
          )}
        >
          {value}
        </span>
        {unit ? <span className="text-sm text-muted">{unit}</span> : null}
      </p>
    </div>
  )
}

function StatsRow() {
  const stats = useLeadStats()
  if (stats.isPending) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[104px] rounded-card" />
        ))}
      </div>
    )
  }
  if (stats.isError) return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
  const s: LeadStats = stats.data
  return (
    <section aria-label="Pipeline at a glance" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="New · 24 h" value={String(s.new_24h)} unit="leads" />
      <Stat
        label="High priority open"
        value={String(s.high_priority_open)}
        unit="leads"
        tone={s.high_priority_open ? 'text-priority-high' : undefined}
      />
      <Stat
        label="Past SLA, unanswered"
        value={String(s.unanswered_past_sla)}
        unit="leads"
        tone={s.unanswered_past_sla ? 'text-priority-critical' : undefined}
      />
      <Stat
        label="Conversion · 30 d"
        value={percent(s.conversion_rate_30d)}
        unit={s.closed_30d ? `of ${s.closed_30d} closed` : 'none closed yet'}
      />
    </section>
  )
}

function NeedsYou() {
  // Reps see their own urgent leads; admins own none, so they see the team's.
  const { isAdmin } = useSession()
  const leads = useListLeads({
    owner: isAdmin ? undefined : 'me',
    priority: ['critical', 'high'],
    status: ['new', 'contacted', 'qualified'],
    sort: 'score',
    page_size: 3,
  })
  return (
    <section aria-labelledby="needs-you" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 id="needs-you" className="text-xl font-bold text-ink">
          {isAdmin ? 'Needs the team now' : 'Needs you now'}
        </h2>
        {leads.data && leads.data.total > 3 ? (
          <Link
            to="/"
            search={isAdmin ? { sort: 'score' } : { owner: 'me', sort: 'score' }}
            className="text-sm font-semibold text-accent hover:underline"
          >
            See all {leads.data.total}
          </Link>
        ) : null}
      </div>
      {leads.isPending ? (
        <div className="grid gap-3 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-36 rounded-card" />
          ))}
        </div>
      ) : leads.isError ? (
        <ErrorState error={leads.error} onRetry={() => void leads.refetch()} />
      ) : leads.data.items.length === 0 ? (
        <EmptyState
          title={isAdmin ? 'Nothing urgent right now' : "You're all caught up"}
          icon="check"
        >
          {isAdmin
            ? 'No open high or critical leads across the team.'
            : 'No high or critical leads are waiting on you.'}{' '}
          New ones appear here, and in the bell, the moment they're scored.
        </EmptyState>
      ) : (
        <ul className="grid gap-3 md:grid-cols-3">
          {leads.data.items.map((lead) => (
            <li key={lead.id}>
              <Link
                to="/leads/$leadId"
                params={{ leadId: lead.id }}
                className="flex h-full flex-col gap-3 rounded-card border border-border bg-surface p-5 transition-colors duration-150 hover:border-accent"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Eyebrow>
                      {lead.source === 'email' ? 'Email' : 'WhatsApp'} ·{' '}
                      {timeAgo(lead.last_message_at ?? lead.created_at)}
                    </Eyebrow>
                    <p dir="auto" className="mt-1 truncate text-lg font-bold text-ink">
                      {leadName(lead)}
                    </p>
                  </div>
                  <ScoreMeter
                    score={lead.score}
                    priority={lead.priority}
                    failed={lead.processing_state === 'failed'}
                  />
                </div>
                <p dir="auto" className="line-clamp-2 text-sm text-body">
                  {lead.summary ?? 'Waiting for the AI summary…'}
                </p>
                {lead.sla_breached ? (
                  <p className="mt-auto flex items-center gap-1.5 text-sm font-semibold text-priority-critical">
                    <Icon name="alert" size={16} /> No response within the SLA
                  </p>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return debounced
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string | undefined
  onChange: (value: string | undefined) => void
  children: React.ReactNode
}) {
  const labelId = useId()
  return (
    <div className="flex flex-col gap-1">
      <span id={labelId} className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
        {label}
      </span>
      <Select
        labelledBy={labelId}
        value={value ?? ''}
        onValueChange={(next) => onChange(next || undefined)}
        className="text-[15px]"
      >
        {children}
      </Select>
    </div>
  )
}

function LeadTable() {
  const search = useSearch({ from: '/app/' })
  const navigate = useNavigate({ from: '/' })
  const { user } = useSession()
  const users = useListAssignableUsers()
  const [query, setQuery] = useState(search.q ?? '')
  // Back/Forward or a link can change q in the URL: show it in the box (state adjusted
  // during render, the React pattern for following a prop).
  const [urlQ, setUrlQ] = useState(search.q)
  if (search.q !== urlQ) {
    setUrlQ(search.q)
    if ((search.q ?? '') !== query) setQuery(search.q ?? '')
  }
  const debounced = useDebounced(query, 300)
  const leads = useListLeads(toListParams(search), {
    query: { placeholderData: (previous) => previous },
  })

  const update = (patch: Partial<LeadSearch>) =>
    void navigate({ search: (current: LeadSearch) => ({ ...current, ...patch, page: patch.page }) })

  useEffect(() => {
    if ((debounced || undefined) !== search.q) update({ q: debounced || undefined })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the typed text settles
  }, [debounced])

  const total = leads.data?.total ?? 0
  const page = search.page ?? 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section aria-labelledby="all-leads" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="all-leads" className="text-xl font-bold text-ink">
          All leads
        </h2>
        <p className="font-mono text-sm text-muted tabular-nums" aria-live="polite">
          {leads.data ? `${total} ${total === 1 ? 'lead' : 'leads'}` : ''}
        </p>
      </div>

      <Card className="flex flex-col gap-4 p-4">
        <label className="relative block">
          <span className="sr-only">Search leads</span>
          <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted">
            <Icon name="search" size={18} />
          </span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name, company, email, phone or summary"
            className="min-h-11 w-full rounded-control border border-border bg-surface pr-3 pl-10 text-[16px] text-ink placeholder:text-muted"
          />
        </label>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <FilterSelect
            label="Source"
            value={search.source}
            onChange={(v) => update({ source: v as LeadSearch['source'] })}
          >
            <option value="">All sources</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </FilterSelect>
          <FilterSelect
            label="Category"
            value={search.category}
            onChange={(v) => update({ category: v as LeadSearch['category'] })}
          >
            <option value="">All categories</option>
            {LEAD_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABELS[c]}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect
            label="Priority"
            value={search.priority}
            onChange={(v) => update({ priority: v as LeadSearch['priority'] })}
          >
            <option value="">Any priority</option>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABELS[p]}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect
            label="Status"
            value={search.status}
            onChange={(v) => update({ status: v as LeadSearch['status'] })}
          >
            <option value="">Any status</option>
            {LEAD_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Owner" value={search.owner} onChange={(v) => update({ owner: v })}>
            <option value="">Anyone</option>
            <option value="me">Me</option>
            <option value="unassigned">Unassigned</option>
            {(users.data ?? [])
              .filter((u) => u.id !== user?.id)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name}
                </option>
              ))}
          </FilterSelect>
          <FilterSelect
            label="Sort"
            value={search.sort ?? 'newest'}
            onChange={(v) => update({ sort: v as LeadSearch['sort'] })}
          >
            <option value="newest">Newest first</option>
            <option value="score">Highest score</option>
            <option value="activity">Latest message</option>
          </FilterSelect>
        </div>
        {hasFilters(search) ? (
          <div>
            <Button
              variant="ghost"
              size="sm"
              icon="close"
              onClick={() => {
                setQuery('')
                void navigate({ search: {} })
              }}
            >
              Clear filters
            </Button>
          </div>
        ) : null}
      </Card>

      {leads.isPending ? (
        <div className="flex flex-col gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-16 rounded-card" />
          ))}
        </div>
      ) : leads.isError ? (
        <ErrorState error={leads.error} onRetry={() => void leads.refetch()} />
      ) : leads.data.items.length === 0 ? (
        hasFilters(search) ? (
          <EmptyState title="No leads match these filters" icon="search">
            Try a broader search, or clear the filters.
          </EmptyState>
        ) : (
          <EmptyState title="No leads yet" icon="mail">
            Leads arrive from your connected mailboxes and WhatsApp. An admin can add a mailbox in
            Settings.
          </EmptyState>
        )
      ) : (
        <>
          <div
            className={cn(
              'hidden overflow-hidden rounded-card border border-border bg-surface md:block',
              leads.isPlaceholderData && 'opacity-60',
            )}
          >
            <table className="w-full text-left">
              <thead className="border-b border-border">
                <tr className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
                  <th scope="col" className="px-4 py-3 font-normal">
                    Lead
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Source
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Category
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Score
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Owner
                  </th>
                  <th scope="col" className="px-4 py-3 font-normal">
                    Last message
                  </th>
                </tr>
              </thead>
              <tbody>
                {leads.data.items.map((lead) => (
                  <tr key={lead.id} className={cn(ROW, 'border-b border-border last:border-0')}>
                    <td className="max-w-xs px-4 py-3">
                      <Link
                        to="/leads/$leadId"
                        params={{ leadId: lead.id }}
                        dir="auto"
                        className={cn(ROW_LINK, 'block font-semibold text-ink')}
                      >
                        {leadName(lead)}
                      </Link>
                      <p dir="auto" className="truncate text-sm text-muted">
                        {lead.summary ?? personLine(lead)}
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <SourceChip source={lead.source} />
                    </td>
                    <td className="px-4 py-3">
                      <CategoryChip category={lead.category} />
                    </td>
                    <td className="px-4 py-3">
                      <ScoreMeter
                        score={lead.score}
                        priority={lead.priority}
                        failed={lead.processing_state === 'failed'}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col items-start gap-1">
                        <StatusChip status={lead.status} />
                        {lead.sla_breached ? (
                          <span className="text-xs font-semibold text-priority-critical">
                            Past SLA
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-body">
                      {lead.owner?.full_name ?? <span className="text-muted">Unassigned</span>}
                    </td>
                    <td className="px-4 py-3 font-mono text-sm text-muted tabular-nums">
                      {timeAgo(lead.last_message_at ?? lead.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ul className="flex flex-col gap-2 md:hidden">
            {leads.data.items.map((lead) => (
              <li key={lead.id}>
                <Link
                  to="/leads/$leadId"
                  params={{ leadId: lead.id }}
                  className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p dir="auto" className="truncate font-semibold text-ink">
                        {leadName(lead)}
                      </p>
                      <p className="font-mono text-xs text-muted">
                        {timeAgo(lead.last_message_at ?? lead.created_at)}
                      </p>
                    </div>
                    <ScoreMeter
                      score={lead.score}
                      priority={lead.priority}
                      failed={lead.processing_state === 'failed'}
                    />
                  </div>
                  <p dir="auto" className="line-clamp-2 text-sm text-body">
                    {lead.summary ?? personLine(lead)}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    <SourceChip source={lead.source} />
                    <CategoryChip category={lead.category} />
                    <StatusChip status={lead.status} />
                    {lead.sla_breached ? <Chip tone="critical">Past SLA</Chip> : null}
                  </div>
                  <p className="text-sm text-muted">
                    {lead.owner ? `Owner: ${lead.owner.full_name}` : 'Unassigned'}
                  </p>
                </Link>
              </li>
            ))}
          </ul>

          {pages > 1 ? (
            <nav aria-label="Pages" className="flex items-center justify-between">
              <Button
                variant="secondary"
                size="sm"
                icon="chevronLeft"
                disabled={page <= 1}
                onClick={() => update({ page: page - 1 > 1 ? page - 1 : undefined })}
              >
                Previous
              </Button>
              <span className="font-mono text-sm text-muted tabular-nums">
                Page {page} of {pages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= pages}
                onClick={() => update({ page: page + 1 })}
              >
                Next <Icon name="chevronRight" size={18} />
              </Button>
            </nav>
          ) : null}
        </>
      )}
    </section>
  )
}
