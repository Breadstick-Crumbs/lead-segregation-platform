import { useState } from 'react'

import {
  Button,
  Card,
  ErrorState,
  SelectField,
  Skeleton,
  TextAreaField,
  TextField,
} from '../../components/ui'
import { SaveBar } from './SaveBar'
import { useSettingsDoc } from './useSettingsDoc'

interface Templates {
  default_language: string
  subject_prefix: string
  templates: Record<string, string>
}

const LANGUAGE_NAMES: Record<string, string> = {
  en: 'English',
  ar: 'Arabic',
  fr: 'French',
  hi: 'Hindi',
  ur: 'Urdu',
}

export function TemplateSettings() {
  const doc = useSettingsDoc<Templates>('auto_reply.templates')
  if (doc.error) return <ErrorState error={doc.error} onRetry={doc.retry} />
  if (!doc.value) return <Skeleton className="h-96 rounded-card" />
  return <TemplateForm doc={doc} saved={doc.value} />
}

function TemplateForm({
  doc,
  saved,
}: {
  doc: ReturnType<typeof useSettingsDoc<Templates>>
  saved: Templates
}) {
  const [draft, setDraft] = useState<Templates>(saved)
  const [newLanguage, setNewLanguage] = useState('')

  const languages = Object.keys(draft.templates)
  const empty = languages.filter((code) => !draft.templates[code]?.trim())
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved)
  const code = newLanguage.trim().toLowerCase()
  const codeError =
    code &&
    (!/^[a-z]{2}$/.test(code)
      ? 'Use a two-letter ISO 639-1 code, like fr'
      : languages.includes(code)
        ? 'That language is there already'
        : null)

  return (
    <Card className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-bold text-ink">Auto-reply templates</h2>
        <p className="text-sm text-muted">
          Sent once to each lead scoring above the threshold, in the language the AI detected, or
          the default. <code className="font-mono">{'{name}'}</code> becomes the customer's first
          name.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Default language"
          value={draft.default_language}
          onChange={(e) => setDraft({ ...draft, default_language: e.target.value })}
        >
          {languages.map((c) => (
            <option key={c} value={c}>
              {LANGUAGE_NAMES[c] ?? c}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Email subject prefix"
          value={draft.subject_prefix}
          onChange={(e) => setDraft({ ...draft, subject_prefix: e.target.value })}
          hint='Replies read "Re: <their subject>"'
        />
      </div>
      {languages.map((c) => (
        <div key={c} className="flex flex-col gap-2">
          <TextAreaField
            label={`${LANGUAGE_NAMES[c] ?? c} (${c})`}
            dir={c === 'ar' || c === 'ur' ? 'rtl' : 'auto'}
            rows={3}
            value={draft.templates[c]}
            onChange={(e) =>
              setDraft({ ...draft, templates: { ...draft.templates, [c]: e.target.value } })
            }
            error={empty.includes(c) ? 'A template needs some text' : null}
          />
          {c !== draft.default_language ? (
            <div>
              <Button
                variant="ghost"
                size="sm"
                icon="close"
                onClick={() => {
                  const { [c]: _removed, ...rest } = draft.templates
                  setDraft({ ...draft, templates: rest })
                }}
              >
                Remove {LANGUAGE_NAMES[c] ?? c}
              </Button>
            </div>
          ) : null}
        </div>
      ))}
      <div className="flex flex-wrap items-end gap-2">
        <div className="w-48">
          <TextField
            label="Add a language"
            placeholder="fr"
            value={newLanguage}
            onChange={(e) => setNewLanguage(e.target.value)}
            error={codeError || null}
            maxLength={2}
          />
        </div>
        <Button
          variant="secondary"
          icon="plus"
          disabled={!code || Boolean(codeError)}
          onClick={() => {
            setDraft({ ...draft, templates: { ...draft.templates, [code]: '' } })
            setNewLanguage('')
          }}
        >
          Add
        </Button>
      </div>
      <SaveBar
        dirty={dirty && empty.length === 0}
        saving={doc.saving}
        onSave={() => doc.save(draft)}
        onReset={() => setDraft(saved)}
      />
    </Card>
  )
}
