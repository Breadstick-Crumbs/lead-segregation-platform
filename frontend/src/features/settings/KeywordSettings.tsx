import { useState } from 'react'

import { Button, Card, ErrorState, Skeleton } from '../../components/ui'
import { SaveBar } from './SaveBar'
import { useSettingsDoc } from './useSettingsDoc'

interface Rule {
  term: string
  points: number
}

export function KeywordSettings() {
  const doc = useSettingsDoc<{ rules: Rule[] }>('scoring.keywords')
  if (doc.error) return <ErrorState error={doc.error} onRetry={doc.retry} />
  if (!doc.value) return <Skeleton className="h-96 rounded-card" />
  return <KeywordForm doc={doc} saved={doc.value.rules} />
}

function KeywordForm({
  doc,
  saved,
}: {
  doc: ReturnType<typeof useSettingsDoc<{ rules: Rule[] }>>
  saved: Rule[]
}) {
  const [rules, setRules] = useState<Rule[]>(saved)

  const invalid = rules.some((r) => !r.term.trim() || r.points < 1 || r.points > 100)
  const dirty = JSON.stringify(rules) !== JSON.stringify(saved)
  const set = (index: number, patch: Partial<Rule>) =>
    setRules(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)))

  return (
    <Card className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-bold text-ink">Keywords</h2>
        <p className="text-sm text-muted">
          Each distinct term found in a lead adds its points to the keyword factor, up to 100.
          English terms match whole words; Arabic terms also match with prefixes like و and ال.
        </p>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2">
        {rules.map((rule, index) => (
          <li key={index} className="flex items-end gap-2">
            <label className="flex flex-1 flex-col gap-1">
              <span className="sr-only">Term {index + 1}</span>
              <input
                dir="auto"
                value={rule.term}
                onChange={(e) => set(index, { term: e.target.value })}
                maxLength={100}
                aria-invalid={!rule.term.trim() || undefined}
                className="min-h-11 rounded-control border border-border bg-surface px-3 text-[16px] text-ink aria-[invalid=true]:border-priority-critical"
              />
            </label>
            <label className="flex w-24 flex-col gap-1">
              <span className="sr-only">Points for {rule.term || `term ${index + 1}`}</span>
              <input
                type="number"
                min={1}
                max={100}
                value={rule.points}
                onChange={(e) => set(index, { points: Number(e.target.value) })}
                className="min-h-11 rounded-control border border-border bg-surface px-3 font-mono text-[16px] text-ink"
              />
            </label>
            <Button
              variant="ghost"
              icon="close"
              aria-label={`Remove ${rule.term || 'term'}`}
              onClick={() => setRules(rules.filter((_, i) => i !== index))}
            />
          </li>
        ))}
      </ul>
      <div>
        <Button
          variant="secondary"
          icon="plus"
          onClick={() => setRules([...rules, { term: '', points: 20 }])}
        >
          Add keyword
        </Button>
      </div>
      {invalid ? (
        <p role="alert" className="text-sm text-priority-critical">
          Every keyword needs a term and 1–100 points.
        </p>
      ) : null}
      <SaveBar
        dirty={dirty && !invalid}
        saving={doc.saving}
        onSave={() => doc.save({ rules: rules.map((r) => ({ ...r, term: r.term.trim() })) })}
        onReset={() => setRules(saved)}
      />
    </Card>
  )
}
