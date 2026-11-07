import type { LeadCategory, Priority } from '../../api/generated'
import { Chip, Eyebrow } from '../../components/ui'
import { cn } from '../../lib/cn'
import { PRIORITY_LABELS, priorityText } from '../../lib/format'
import { CategoryChip } from './LeadBits'

const FACTORS: { key: string; label: string }[] = [
  { key: 'intent', label: 'Intent' },
  { key: 'urgency', label: 'Urgency' },
  { key: 'business_value', label: 'Business value' },
  { key: 'keywords', label: 'Keywords' },
  { key: 'company', label: 'Company' },
  { key: 'history', label: 'History' },
]

type Factor = { value?: number; weight?: number; matched?: string[] } | undefined

/** The score with its factor breakdown: on a lead's page and in "Try a lead". */
export function ScoreCard({
  score,
  priority,
  category,
  language,
  breakdown,
  rationale,
  model,
  promptVersion,
  title = 'Lead score',
}: {
  score: number | null | undefined
  priority: Priority | null | undefined
  category: LeadCategory | null | undefined
  language: string | null | undefined
  breakdown: Record<string, unknown> | null
  rationale: string | null
  model: string | null
  promptVersion: string | null
  title?: string
}) {
  const factors = (breakdown ?? {}) as Record<string, Factor>
  const cap = (breakdown?.cap as { reason: string; max: number } | null | undefined) ?? null
  return (
    <div className="relative">
      <div aria-hidden className="absolute inset-0 rotate-2 rounded-plate bg-plate" />
      <section
        className="relative flex flex-col gap-5 rounded-card border border-border bg-surface p-6"
        aria-labelledby="score-title"
      >
        <div>
          <Eyebrow>
            <span id="score-title">{title}</span>
          </Eyebrow>
          {score == null ? (
            <p className="mt-2 text-body">
              Scoring… this takes a few seconds after a message arrives.
            </p>
          ) : (
            <p className="mt-1 flex items-baseline gap-2">
              <span className="text-7xl leading-none font-bold tracking-[-0.03em] tabular-nums text-ink">
                {score}
              </span>
              <span className="text-muted">/100</span>
              {priority ? (
                <span className={cn('ml-auto text-lg font-bold', priorityText(priority))}>
                  {PRIORITY_LABELS[priority]}
                </span>
              ) : null}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <CategoryChip category={category ?? null} />
            {language ? <Chip>{language === 'ar' ? 'Arabic' : language.toUpperCase()}</Chip> : null}
          </div>
        </div>
        {breakdown ? (
          <>
            <ul className="flex flex-col gap-2.5" aria-label="Score factors">
              {FACTORS.map(({ key, label }) => {
                const factor = factors[key]
                const value = factor?.value ?? 0
                return (
                  <li key={key}>
                    <div className="flex justify-between text-sm">
                      <span className="text-body">
                        {label}
                        <span className="ml-1 font-mono text-xs text-muted">
                          ×{factor?.weight ?? 0}
                        </span>
                      </span>
                      <span className="font-mono font-semibold text-ink tabular-nums">{value}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-plate" aria-hidden>
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${value}%` }}
                      />
                    </div>
                  </li>
                )
              })}
            </ul>
            {factors.keywords?.matched?.length ? (
              <p className="text-sm text-muted">
                Matched:{' '}
                <span dir="auto" className="text-body">
                  {factors.keywords.matched.join(', ')}
                </span>
              </p>
            ) : null}
            {cap ? (
              <p className="text-sm text-muted">
                Capped at {cap.max} (
                {cap.reason === 'spam' ? 'spam' : 'support requests stay out of sales priority'}).
              </p>
            ) : null}
            {rationale ? (
              <blockquote className="border-l-2 border-accent pl-3 text-body italic">
                “{rationale}”
              </blockquote>
            ) : null}
            {model ? (
              <p className="font-mono text-[11px] text-muted">
                {model} · prompt {promptVersion}
              </p>
            ) : null}
          </>
        ) : null}
      </section>
    </div>
  )
}
