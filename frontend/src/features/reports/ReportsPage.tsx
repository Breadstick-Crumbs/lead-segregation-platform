import { Link, useNavigate, useSearch } from '@tanstack/react-router'

import { useLeadReport, type Report } from '../../api/generated'
import { Card, ErrorState, Eyebrow, PageHeader, ROW, ROW_LINK, Skeleton } from '../../components/ui'
import { cn } from '../../lib/cn'
import { CATEGORY_LABELS, STATUS_LABELS, percent } from '../../lib/format'
import { ScoreMeter } from '../leads/LeadBits'
import { BarList, DataTable, Legend, StackedColumns } from './charts'
import { REPORT_PERIODS, duration } from './format'

const SERIES = [
  { key: 'email' as const, label: 'Email', color: 'var(--color-series-email)' },
  { key: 'whatsapp' as const, label: 'WhatsApp', color: 'var(--color-series-whatsapp)' },
]

function dayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function ReportsPage() {
  const { days = 30 } = useSearch({ from: '/app/reports' })
  const navigate = useNavigate({ from: '/reports' })
  const report = useLeadReport({ days }, { query: { placeholderData: (previous) => previous } })

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Reporting"
        title="Reports"
        accent={`the last ${days} days`}
        actions={
          <div role="group" aria-label="Period" className="inline-flex rounded-full bg-plate p-1">
            {REPORT_PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                aria-pressed={days === p}
                onClick={() => void navigate({ search: { days: p } })}
                className={cn(
                  'min-h-11 rounded-full px-4 text-sm font-semibold transition-colors duration-150',
                  days === p ? 'bg-surface text-ink' : 'text-muted hover:text-ink',
                )}
              >
                {p} days
              </button>
            ))}
          </div>
        }
      />
      {report.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-72 rounded-card" />
          ))}
        </div>
      ) : report.isError ? (
        <ErrorState error={report.error} onRetry={() => void report.refetch()} />
      ) : (
        <div className={cn('flex flex-col gap-6', report.isPlaceholderData && 'opacity-60')}>
          <Summary report={report.data} />
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <Volume report={report.data} />
            <Categories report={report.data} />
          </div>
          <div className="grid items-start gap-6 lg:grid-cols-2">
            <FunnelCard report={report.data} />
            <Response report={report.data} />
          </div>
          <HighValueCard report={report.data} />
        </div>
      )}
    </div>
  )
}

function Tile({
  label,
  value,
  unit,
  note,
}: {
  label: string
  value: string
  unit?: string
  note?: string
}) {
  return (
    <div className="rounded-card bg-plate px-5 py-4">
      <Eyebrow>{label}</Eyebrow>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span className="text-5xl leading-none font-bold tracking-[-0.02em] text-ink">{value}</span>
        {unit ? <span className="text-sm text-muted">{unit}</span> : null}
      </p>
      {note ? <p className="mt-2 text-sm text-muted">{note}</p> : null}
    </div>
  )
}

function Summary({ report }: { report: Report }) {
  const r = report.response
  const median = duration(r.median_minutes)
  return (
    <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Leads"
        value={String(report.total)}
        note={`${report.spam} spam · ${report.auto_replies_sent} auto-replies sent`}
      />
      <Tile
        label="Conversion"
        value={percent(report.funnel.conversion_rate)}
        note={`${report.funnel.converted} of ${report.funnel.created} genuine leads`}
      />
      <Tile
        label="Median first response"
        value={median.value}
        unit={median.unit}
        note={`${r.responded} answered · ${r.unanswered} with no response yet`}
      />
      <Tile
        label={`Within ${r.sla_minutes} min SLA`}
        value={percent(r.within_sla_rate)}
        note={
          r.urgent
            ? `${r.urgent_within_sla} of ${r.urgent} high or critical leads`
            : 'No high or critical leads due yet'
        }
      />
    </section>
  )
}

function Volume({ report }: { report: Report }) {
  const data = report.volume.map((d) => ({
    id: d.date,
    label: dayLabel(d.date),
    email: d.email,
    whatsapp: d.whatsapp,
  }))
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-ink">Leads by source</h2>
          <p className="text-sm text-muted">New leads per day ({report.period.timezone} time)</p>
        </div>
        <Legend
          items={SERIES.map((s) => ({
            label: s.label,
            color: s.color,
            value: report.by_source[s.key] ?? 0,
          }))}
        />
      </div>
      {report.total === 0 ? (
        <p className="py-16 text-center text-sm text-muted">No leads in this period.</p>
      ) : (
        <StackedColumns
          data={data}
          series={SERIES}
          ariaLabel={`New leads per day by source over the last ${report.period.days} days`}
        />
      )}
      <DataTable
        caption="New leads per day by source"
        columns={['Day', 'Email', 'WhatsApp']}
        rows={report.volume.map((d) => [dayLabel(d.date), d.email, d.whatsapp])}
      />
    </Card>
  )
}

function Categories({ report }: { report: Report }) {
  const total = report.categories.reduce((sum, c) => sum + c.count, 0)
  const rows = report.categories.map((c) => ({
    key: c.category ?? 'none',
    label: c.category ? CATEGORY_LABELS[c.category] : 'Not classified',
    value: c.count,
    note: total ? `${Math.round((c.count / total) * 100)}%` : undefined,
  }))
  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-bold text-ink">Categories</h2>
        <p className="text-sm text-muted">What the AI classified the leads as</p>
      </div>
      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">No leads in this period.</p>
      ) : (
        <BarList rows={rows} ariaLabel="Leads by category" />
      )}
      <DataTable
        caption="Leads by category"
        columns={['Category', 'Leads']}
        rows={rows.map((r) => [r.label, r.value])}
      />
    </Card>
  )
}

function FunnelCard({ report }: { report: Report }) {
  const f = report.funnel
  const share = (n: number) => (f.created ? `${Math.round((n / f.created) * 100)}%` : undefined)
  const rows = [
    { key: 'created', label: 'New leads (not spam)', value: f.created },
    {
      key: 'contacted',
      label: STATUS_LABELS.contacted,
      value: f.contacted,
      note: share(f.contacted),
    },
    {
      key: 'qualified',
      label: STATUS_LABELS.qualified,
      value: f.qualified,
      note: share(f.qualified),
    },
    {
      key: 'converted',
      label: STATUS_LABELS.converted,
      value: f.converted,
      note: share(f.converted),
    },
  ]
  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-bold text-ink">Conversion funnel</h2>
        <p className="text-sm text-muted">
          The furthest stage each lead reached. {f.lost} lost along the way.
        </p>
      </div>
      <BarList rows={rows} max={f.created || 1} ariaLabel="Conversion funnel" />
      <DataTable
        caption="Conversion funnel"
        columns={['Stage', 'Leads']}
        rows={[...rows.map((r) => [r.label, r.value]), ['Lost', f.lost]]}
      />
    </Card>
  )
}

function Response({ report }: { report: Report }) {
  const r = report.response
  const p90 = duration(r.p90_minutes)
  const rows = r.by_source.map((s) => ({
    key: s.source,
    label: s.source === 'email' ? 'Email' : 'WhatsApp',
    value: Math.round(s.median_minutes ?? 0), // bar length, in minutes
    display: s.responded ? duration(s.median_minutes).value : '—',
    note: s.responded
      ? `${duration(s.median_minutes).unit} · ${s.responded} answered`
      : 'none answered yet',
  }))
  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-bold text-ink">Time to first response</h2>
        <p className="text-sm text-muted">
          From arrival to a person replying or moving the lead on. Marking it spam or lost doesn't
          count.
        </p>
      </div>
      <p className="flex items-baseline gap-2">
        <span className="text-sm text-muted">Slowest 10% wait over</span>
        <span className="text-2xl font-bold text-ink">{p90.value}</span>
        <span className="text-sm text-muted">{p90.unit}</span>
      </p>
      <BarList rows={rows} ariaLabel="Median first response by source, in minutes" />
      <DataTable
        caption="First response by source"
        columns={['Source', 'Median (min)', 'Answered']}
        rows={r.by_source.map((s) => [
          s.source === 'email' ? 'Email' : 'WhatsApp',
          s.median_minutes ?? '—',
          s.responded,
        ])}
      />
    </Card>
  )
}

function HighValueCard({ report }: { report: Report }) {
  const h = report.high_value
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-ink">High-value leads</h2>
          <p className="text-sm text-muted">Scored {h.threshold} or more</p>
        </div>
        <dl className="flex gap-8">
          <div>
            <dt className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">Leads</dt>
            <dd className="text-2xl font-bold text-ink">
              {h.count}{' '}
              <span className="text-sm font-normal text-muted">
                {percent(h.share_of_leads)} of genuine leads
              </span>
            </dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
              Converted
            </dt>
            <dd className="text-2xl font-bold text-ink">
              {h.converted}{' '}
              <span className="text-sm font-normal text-muted">{percent(h.conversion_rate)}</span>
            </dd>
          </div>
        </dl>
      </div>
      {h.top.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">No high-value leads in this period.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead>
              <tr className="border-b border-border font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
                <th scope="col" className="py-2 pr-4 font-normal">
                  Lead
                </th>
                <th scope="col" className="py-2 pr-4 font-normal">
                  Category
                </th>
                <th scope="col" className="py-2 pr-4 font-normal">
                  Score
                </th>
                <th scope="col" className="py-2 pr-4 font-normal">
                  Status
                </th>
                <th scope="col" className="py-2 font-normal">
                  Owner
                </th>
              </tr>
            </thead>
            <tbody>
              {h.top.map((lead) => (
                <tr key={lead.id} className={cn(ROW, 'border-b border-border last:border-0')}>
                  <td className="py-2.5 pr-4">
                    <Link
                      to="/leads/$leadId"
                      params={{ leadId: lead.id }}
                      dir="auto"
                      className={cn(ROW_LINK, 'font-semibold text-ink')}
                    >
                      {lead.display_name}
                    </Link>
                  </td>
                  <td className="py-2.5 pr-4 text-sm text-body">
                    {lead.category ? CATEGORY_LABELS[lead.category] : '—'}
                  </td>
                  <td className="py-2.5 pr-4">
                    <ScoreMeter score={lead.score} priority={lead.priority} />
                  </td>
                  <td
                    className={cn(
                      'py-2.5 pr-4 text-sm',
                      lead.status === 'converted' ? 'text-success' : 'text-body',
                    )}
                  >
                    {STATUS_LABELS[lead.status]}
                  </td>
                  <td className="py-2.5 text-sm text-body">
                    {lead.owner_name ?? <span className="text-muted">Unassigned</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
