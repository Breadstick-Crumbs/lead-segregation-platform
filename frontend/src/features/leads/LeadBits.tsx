import type { Channel, LeadCategory, LeadStatus, Priority } from '../../api/generated'
import { Icon } from '../../components/Icon'
import { Chip } from '../../components/ui'
import { cn } from '../../lib/cn'
import {
  CATEGORY_LABELS,
  CHANNEL_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  priorityText,
} from '../../lib/format'

export function SourceChip({ source }: { source: Channel }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full bg-plate px-2.5 py-0.5 text-[13px] font-semibold text-body"
      title={CHANNEL_LABELS[source]}
    >
      <Icon name={source === 'email' ? 'mail' : 'whatsapp'} size={14} />
      <span>{CHANNEL_LABELS[source]}</span>
    </span>
  )
}

const CATEGORY_TONE: Record<LeadCategory, 'accent' | 'neutral' | 'low'> = {
  sales_opportunity: 'accent',
  demo_request: 'accent',
  partnership_inquiry: 'accent',
  support_request: 'neutral',
  general_inquiry: 'neutral',
  spam: 'low',
}

export function CategoryChip({ category }: { category: LeadCategory | null | undefined }) {
  if (!category) return <Chip>Not classified yet</Chip>
  return <Chip tone={CATEGORY_TONE[category]}>{CATEGORY_LABELS[category]}</Chip>
}

const STATUS_TONE: Record<LeadStatus, 'accent' | 'neutral' | 'success' | 'low'> = {
  // Neutral, so a berry chip beside the red "Past SLA" one can't be confused with it.
  new: 'neutral',
  contacted: 'neutral',
  qualified: 'neutral',
  converted: 'success',
  lost: 'low',
  spam: 'low',
}

export function StatusChip({ status }: { status: LeadStatus }) {
  return <Chip tone={STATUS_TONE[status]}>{STATUS_LABELS[status]}</Chip>
}

const PRIORITY_BAR: Record<Priority, string> = {
  critical: 'bg-priority-critical',
  high: 'bg-priority-high',
  medium: 'bg-priority-medium',
  low: 'bg-priority-low',
}

/** The score as a mono number over a bar in its priority colour, with the priority named. */
export function ScoreMeter({
  score,
  priority,
  failed = false,
  className,
}: {
  score: number | null | undefined
  priority: Priority | null | undefined
  failed?: boolean
  className?: string
}) {
  if (score == null || !priority) {
    return (
      <span
        className={cn(
          'font-mono text-sm',
          failed ? 'text-priority-critical' : 'text-muted',
          className,
        )}
        title={failed ? 'The AI could not score this lead; open it for the reason' : undefined}
      >
        {failed ? 'not scored' : 'scoring…'}
      </span>
    )
  }
  return (
    <div className={cn('flex w-24 flex-col gap-1', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-lg font-bold tabular-nums text-ink">{score}</span>
        <span className={cn('text-[12px] font-semibold', priorityText(priority))}>
          {PRIORITY_LABELS[priority]}
        </span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-plate"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score}
        aria-label={`Score ${score} of 100, ${PRIORITY_LABELS[priority]} priority`}
      >
        <div
          className={cn('h-full rounded-full', PRIORITY_BAR[priority])}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  )
}
