import { useEffect, useMemo, useState } from 'react'

import { usePreviewScoring, type ScoringThresholds, type ScoringWeights } from '../../api/generated'
import { Card, ErrorState, Eyebrow, Skeleton } from '../../components/ui'
import { cn } from '../../lib/cn'
import { PRIORITY_LABELS, priorityText } from '../../lib/format'
import { SaveBar } from './SaveBar'
import { useSettingsDoc } from './useSettingsDoc'

type Weights = Required<ScoringWeights>
type Thresholds = Required<ScoringThresholds>

const WEIGHTS: { key: keyof Weights; label: string; hint: string }[] = [
  { key: 'intent', label: 'Intent', hint: 'How clearly they want to buy (AI)' },
  { key: 'urgency', label: 'Urgency', hint: 'Timeline pressure (AI)' },
  { key: 'business_value', label: 'Business value', hint: 'Deal size and budget (AI)' },
  { key: 'keywords', label: 'Keywords', hint: 'Terms like quote, RFP, demo' },
  { key: 'company', label: 'Company', hint: 'Corporate domain, company, title' },
  { key: 'history', label: 'History', hint: 'Earlier leads and similar wins' },
]

const LIMITS: Record<keyof Thresholds, [number, number]> = {
  auto_reply_above: [0, 100],
  high_at: [0, 100],
  critical_at: [0, 100],
  support_score_cap: [0, 100],
  spam_score_cap: [0, 100],
  sla_minutes: [1, 10080],
}

function thresholdError(t: Thresholds): string | null {
  for (const [key, [min, max]] of Object.entries(LIMITS) as [
    keyof Thresholds,
    [number, number],
  ][]) {
    const value = t[key]
    if (!Number.isInteger(value) || value < min || value > max) {
      return `Every threshold needs a whole number: ${key.replaceAll('_', ' ')} must be ${min}–${max}.`
    }
  }
  if (!(t.auto_reply_above < t.high_at && t.high_at <= t.critical_at)) {
    return 'Keep the order: auto-reply below high, and high at or below critical.'
  }
  return null
}

export function ScoringSettings() {
  const weightsDoc = useSettingsDoc<Weights>('scoring.weights')
  const thresholdsDoc = useSettingsDoc<Thresholds>('scoring.thresholds')
  if (weightsDoc.error) return <ErrorState error={weightsDoc.error} onRetry={weightsDoc.retry} />
  if (!weightsDoc.value || !thresholdsDoc.value) return <Skeleton className="h-96 rounded-card" />
  return (
    <ScoringForm
      weightsDoc={weightsDoc}
      thresholdsDoc={thresholdsDoc}
      savedWeights={weightsDoc.value}
      savedThresholds={thresholdsDoc.value}
    />
  )
}

function ScoringForm({
  weightsDoc,
  thresholdsDoc,
  savedWeights,
  savedThresholds,
}: {
  weightsDoc: ReturnType<typeof useSettingsDoc<Weights>>
  thresholdsDoc: ReturnType<typeof useSettingsDoc<Thresholds>>
  savedWeights: Weights
  savedThresholds: Thresholds
}) {
  const [weights, setWeights] = useState<Weights>(savedWeights)
  const [thresholds, setThresholds] = useState<Thresholds>(savedThresholds)

  const total = Object.values(weights).reduce((sum, v) => sum + v, 0)
  const dirty =
    JSON.stringify(weights) !== JSON.stringify(savedWeights) ||
    JSON.stringify(thresholds) !== JSON.stringify(savedThresholds)
  const problem =
    total === 0 ? 'At least one weight must be above zero.' : thresholdError(thresholds)

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
      <div className="flex flex-col gap-6">
        <Card className="flex flex-col gap-5">
          <div>
            <h2 className="text-lg font-bold text-ink">Factor weights</h2>
            <p className="text-sm text-muted">
              The score is the weighted average of six factors, each 0–100. Weights are relative:
              these add up to {total}.
            </p>
          </div>
          {WEIGHTS.map(({ key, label, hint }) => (
            <div key={key} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1">
              <label htmlFor={`w-${key}`} className="font-semibold text-ink">
                {label}
                <span className="block text-sm font-normal text-muted">{hint}</span>
              </label>
              <span className="font-mono text-lg font-bold tabular-nums text-ink">
                {total ? Math.round((weights[key] / total) * 100) : 0}%
              </span>
              <input
                id={`w-${key}`}
                type="range"
                min={0}
                max={100}
                value={weights[key]}
                onChange={(e) => setWeights({ ...weights, [key]: Number(e.target.value) })}
                className="col-span-2 h-11 accent-[var(--color-accent)]"
              />
            </div>
          ))}
        </Card>

        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-bold text-ink">Thresholds</h2>
            <p className="text-sm text-muted">What each score triggers.</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {(
              [
                [
                  'auto_reply_above',
                  'Auto-reply above',
                  'The spec: reply when the score is above 20',
                ],
                ['high_at', 'High priority from', 'Alerts the owner'],
                ['critical_at', 'Critical from', 'Escalates to managers and chat'],
                ['sla_minutes', 'SLA (minutes)', 'Escalate if nobody answers within this'],
                [
                  'support_score_cap',
                  'Support requests cap',
                  'Keeps support out of sales priority',
                ],
                ['spam_score_cap', 'Spam cap', 'Spam is never replied to or alerted'],
              ] as const
            ).map(([key, label, hint]) => (
              <label key={key} className="flex flex-col gap-1.5">
                <span className="text-sm font-semibold text-ink">{label}</span>
                <input
                  type="number"
                  inputMode="numeric"
                  min={key === 'sla_minutes' ? 1 : 0}
                  max={key === 'sla_minutes' ? 10080 : 100}
                  value={thresholds[key]}
                  onChange={(e) => setThresholds({ ...thresholds, [key]: Number(e.target.value) })}
                  className="min-h-11 rounded-control border border-border bg-surface px-3 font-mono text-[16px] text-ink"
                />
                <span className="text-sm text-muted">{hint}</span>
              </label>
            ))}
          </div>
        </Card>
        {problem ? (
          <p role="alert" className="text-sm text-priority-critical">
            {problem}
          </p>
        ) : null}
        <SaveBar
          dirty={dirty && !problem}
          saving={weightsDoc.saving || thresholdsDoc.saving}
          onSave={async () => {
            await weightsDoc.save(weights)
            await thresholdsDoc.save(thresholds)
          }}
          onReset={() => {
            setWeights(savedWeights)
            setThresholds(savedThresholds)
          }}
        />
      </div>
      <Preview weights={weights} thresholds={thresholds} disabled={Boolean(problem)} />
    </div>
  )
}

function Preview({
  weights,
  thresholds,
  disabled,
}: {
  weights: Weights
  thresholds: Thresholds
  disabled: boolean
}) {
  const preview = usePreviewScoring()
  const request = useMemo(() => ({ weights, thresholds }), [weights, thresholds])
  const { mutate } = preview

  useEffect(() => {
    if (disabled) return
    const timer = setTimeout(() => mutate({ data: request }), 350)
    return () => clearTimeout(timer)
  }, [request, disabled, mutate])

  return (
    <Card as="aside" className="flex flex-col gap-4 lg:sticky lg:top-28">
      <div>
        <Eyebrow>Live preview</Eyebrow>
        <h2 className="mt-1 text-lg font-bold text-ink">Your last 20 leads, rescored</h2>
        {preview.data ? (
          <p className="mt-1 text-sm text-muted">
            <span className="font-semibold text-ink">{preview.data.changed}</span> would change
            priority. Nothing is saved until you press Save.
          </p>
        ) : null}
      </div>
      {preview.isError ? (
        <p className="text-sm text-priority-critical">
          {preview.error instanceof Error ? preview.error.message : 'Preview failed'}
        </p>
      ) : !preview.data ? (
        <Skeleton className="h-48" />
      ) : preview.data.items.length === 0 ? (
        <p className="text-sm text-muted">No scored leads yet, so there's nothing to preview.</p>
      ) : (
        <ul
          className={cn('flex flex-col divide-y divide-border', preview.isPending && 'opacity-60')}
        >
          {preview.data.items.map((item) => {
            const moved = item.new_priority !== item.current_priority
            return (
              <li key={item.lead_id} className="flex items-center justify-between gap-3 py-2">
                <span className="truncate text-sm text-ink">{item.display_name}</span>
                <span className="flex shrink-0 items-center gap-2 font-mono text-sm tabular-nums">
                  <span className="text-muted">{item.current_score}</span>
                  <span aria-hidden className="text-muted">
                    →
                  </span>
                  <span
                    className={cn(
                      'font-bold',
                      moved ? priorityText(item.new_priority) : 'text-ink',
                    )}
                  >
                    {item.new_score}
                  </span>
                  {moved ? (
                    <span className={cn('text-xs font-semibold', priorityText(item.new_priority))}>
                      {PRIORITY_LABELS[item.new_priority]}
                    </span>
                  ) : null}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
