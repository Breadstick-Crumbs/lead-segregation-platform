import { Link } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'

import {
  usePreviewLead,
  type Channel,
  type LeadPreviewRead,
  type LeadPreviewRequest,
} from '../../api/generated'
import { Icon } from '../../components/Icon'
import {
  Button,
  Card,
  Chip,
  Eyebrow,
  PageHeader,
  Skeleton,
  TextAreaField,
  TextField,
} from '../../components/ui'
import { cn } from '../../lib/cn'
import { StatusChip } from '../leads/LeadBits'
import { ScoreCard } from '../leads/ScoreCard'

interface Draft {
  channel: Channel
  sender_name: string
  sender_address: string
  subject: string
  text: string
}

const EXAMPLES: { label: string; draft: Draft }[] = [
  {
    label: 'Sales email',
    draft: {
      channel: 'email',
      sender_name: 'Omar Haddad',
      sender_address: 'omar.haddad@gulfbuild.ae',
      subject: 'Quotation for 40 seats',
      text: 'Hi, we are rolling out a CRM across our Dubai and Abu Dhabi branches and need pricing for 40 seats. Budget is approved and we want to sign before the end of the month. Could you send a quote and book a demo this week?\n\nOmar Haddad\nProcurement Manager, Gulf Build LLC',
    },
  },
  {
    label: 'Arabic WhatsApp',
    draft: {
      channel: 'whatsapp',
      sender_name: 'فاطمة المنصوري',
      sender_address: '+971501234567',
      subject: '',
      text: 'مرحبا، نحتاج عرض سعر لنظام إدارة العملاء لفريق مبيعات من ١٥ شخص. هل يمكن التواصل معي اليوم؟',
    },
  },
  {
    label: 'Support request',
    draft: {
      channel: 'email',
      sender_name: 'Sara Ali',
      sender_address: 'sara.ali@gmail.com',
      subject: "Can't sign in",
      text: "Hello, I can't sign in to my account since this morning. It keeps saying my password is wrong even after resetting it. Please help.",
    },
  },
  {
    label: 'Spam',
    draft: {
      channel: 'email',
      sender_name: 'Rank Boost',
      sender_address: 'offers@rankboost.biz',
      subject: 'Page 1 on Google in 7 days!!!',
      text: 'Boost your Google ranking to #1 in 7 days guaranteed! Cheap SEO packages from $49. Reply YES to start today.',
    },
  },
]

const EMPTY: Draft = {
  channel: 'email',
  sender_name: '',
  sender_address: '',
  subject: '',
  text: '',
}

const ALERTS: Record<string, { label: string; detail: string }> = {
  escalation: {
    label: 'Escalation',
    detail: 'The owner, the managers on the escalation list, and Slack or Teams.',
  },
  high_priority_lead: {
    label: 'High-priority alert',
    detail: 'The owner, in the app and by email.',
  },
}

export function TryLeadPage() {
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const preview = usePreviewLead()

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  function run(event: FormEvent) {
    event.preventDefault()
    const body: LeadPreviewRequest = {
      channel: draft.channel,
      sender_address: draft.sender_address,
      text: draft.text,
      sender_name: draft.sender_name.trim() || null,
      subject: draft.channel === 'email' ? draft.subject.trim() || null : null,
    }
    preview.mutate({ data: body })
  }

  const isEmail = draft.channel === 'email'
  return (
    <div className="flex flex-col gap-8">
      <PageHeader eyebrow="AI pipeline" title="Try a lead" accent="see what the AI decides" />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)]">
        <Card as="section" className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">Start from an example:</span>
            {EXAMPLES.map((example) => (
              <button
                key={example.label}
                type="button"
                onClick={() => {
                  setDraft(example.draft)
                  preview.reset()
                }}
                className="min-h-9 rounded-full border border-border px-3 text-sm font-semibold text-body transition-colors duration-150 hover:border-accent hover:text-accent"
              >
                {example.label}
              </button>
            ))}
          </div>

          <form onSubmit={run} className="flex flex-col gap-4">
            <div
              role="group"
              aria-label="Channel"
              className="inline-flex w-fit rounded-full bg-plate p-1"
            >
              {(['email', 'whatsapp'] as const).map((channel) => (
                <button
                  key={channel}
                  type="button"
                  aria-pressed={draft.channel === channel}
                  onClick={() => set('channel', channel)}
                  className={cn(
                    'flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold transition-colors duration-150',
                    draft.channel === channel ? 'bg-surface text-ink' : 'text-muted hover:text-ink',
                  )}
                >
                  <Icon name={channel === 'email' ? 'mail' : 'whatsapp'} size={16} />
                  {channel === 'email' ? 'Email' : 'WhatsApp'}
                </button>
              ))}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Sender name"
                dir="auto"
                value={draft.sender_name}
                onChange={(e) => set('sender_name', e.target.value)}
                maxLength={300}
              />
              <TextField
                label={isEmail ? 'Email address' : 'WhatsApp number'}
                type={isEmail ? 'email' : 'tel'}
                required
                value={draft.sender_address}
                onChange={(e) => set('sender_address', e.target.value)}
                placeholder={isEmail ? 'name@company.com' : '+971501234567'}
                hint={
                  isEmail ? 'A company domain counts towards the score; Gmail does not.' : undefined
                }
                maxLength={320}
              />
            </div>
            {isEmail ? (
              <TextField
                label="Subject"
                dir="auto"
                value={draft.subject}
                onChange={(e) => set('subject', e.target.value)}
                maxLength={300}
              />
            ) : null}
            <TextAreaField
              label="Message"
              dir="auto"
              required
              rows={7}
              maxLength={4000}
              value={draft.text}
              onChange={(e) => set('text', e.target.value)}
              hint="Any language. It goes to OpenAI exactly as a real lead's message would."
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="flex items-center gap-1.5 text-sm text-muted">
                <Icon name="check" size={16} /> Nothing is stored, sent or assigned.
              </p>
              <Button type="submit" icon="sparkle" busy={preview.isPending}>
                Run the pipeline
              </Button>
            </div>
          </form>
        </Card>

        <div aria-live="polite" className="flex flex-col gap-4 lg:sticky lg:top-28">
          {preview.isPending ? (
            <Card className="flex flex-col gap-3">
              <Eyebrow>Classifying with OpenAI…</Eyebrow>
              <Skeleton className="h-16" />
              <Skeleton className="h-40" />
            </Card>
          ) : preview.isError ? (
            <Card className="flex flex-col gap-2">
              <p className="font-semibold text-priority-critical">That didn't work</p>
              <p className="text-body">
                {preview.error instanceof Error ? preview.error.message : 'Please try again.'}
              </p>
            </Card>
          ) : preview.data ? (
            <Result result={preview.data} />
          ) : (
            <Intro />
          )}
        </div>
      </div>
    </div>
  )
}

function Intro() {
  return (
    <Card className="flex flex-col gap-3">
      <Eyebrow>What you'll see</Eyebrow>
      <ul className="flex flex-col gap-2 text-body">
        {[
          'The category OpenAI picks, with its confidence and reasoning',
          'The 0–100 score and how each of the six factors added to it',
          'Whether it gets the automatic reply, and the exact text',
          'Who would be alerted',
          'Similar past leads, found by meaning (pgvector)',
        ].map((line) => (
          <li key={line} className="flex gap-2">
            <Icon name="check" size={18} className="mt-0.5 shrink-0 text-accent" />
            {line}
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted">
        It runs the same code as a real lead, with your current Settings, but nothing is saved.
      </p>
    </Card>
  )
}

function Result({ result }: { result: LeadPreviewRead }) {
  const alert = result.alert ? ALERTS[result.alert] : undefined
  return (
    <>
      <ScoreCard
        title="Would score"
        score={result.score}
        priority={result.priority}
        category={result.category}
        language={result.language}
        breakdown={result.breakdown}
        rationale={result.rationale}
        model={result.model}
        promptVersion={result.prompt_version}
      />
      <Card className="flex flex-col gap-5">
        <div>
          <Eyebrow>Summary</Eyebrow>
          <p dir="auto" className="mt-1 text-ink">
            {result.summary}
          </p>
          {result.company_name || result.job_title ? (
            <p className="mt-1 text-sm text-muted">
              Detected: {[result.job_title, result.company_name].filter(Boolean).join(', ')}
            </p>
          ) : null}
          <p className="mt-1 font-mono text-xs text-muted">
            Category confidence {Math.round(result.category_confidence * 100)}%
          </p>
        </div>

        <div>
          <Eyebrow>Automatic reply</Eyebrow>
          {result.auto_reply ? (
            <>
              <p className="mt-1 text-sm text-body">
                Sent at once on the same channel, because the score is above the threshold.
              </p>
              <p
                dir="auto"
                className="mt-2 rounded-control bg-accent-tint px-4 py-3 whitespace-pre-line text-ink"
              >
                {result.auto_reply}
              </p>
            </>
          ) : (
            <p className="mt-1 text-body">
              None.{' '}
              {result.category === 'spam'
                ? 'Spam is never answered: a reply would confirm the address.'
                : 'The score is at or below the auto-reply threshold.'}
            </p>
          )}
        </div>

        <div>
          <Eyebrow>Alert</Eyebrow>
          {alert ? (
            <p className="mt-1 text-body">
              <Chip tone={result.alert === 'escalation' ? 'critical' : 'high'}>{alert.label}</Chip>{' '}
              {alert.detail}
            </p>
          ) : (
            <p className="mt-1 text-body">No alert; it waits in the lead list.</p>
          )}
        </div>

        <div>
          <Eyebrow>Similar past leads</Eyebrow>
          {result.similar.length === 0 ? (
            <p className="mt-1 text-body">Nothing similar yet.</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {result.similar.map((s) => (
                <li key={s.id}>
                  <Link
                    to="/leads/$leadId"
                    params={{ leadId: s.id }}
                    className="flex items-center justify-between gap-3 rounded-control border border-border px-3 py-2 transition-colors duration-150 hover:border-accent"
                  >
                    <span className="min-w-0">
                      <span dir="auto" className="block truncate font-semibold text-ink">
                        {s.display_name}
                      </span>
                      <span dir="auto" className="block truncate text-sm text-muted">
                        {s.summary}
                      </span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end gap-1">
                      <span className="font-mono text-xs text-muted">
                        {Math.round(s.similarity * 100)}% alike
                      </span>
                      <StatusChip status={s.status} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </>
  )
}
