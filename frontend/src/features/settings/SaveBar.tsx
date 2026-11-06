import { useState } from 'react'

import { Button } from '../../components/ui'

/** Save button with the outcome beside it: saved, or what to fix. */
export function SaveBar({
  dirty,
  saving,
  onSave,
  onReset,
}: {
  dirty: boolean
  saving: boolean
  onSave: () => Promise<unknown>
  onReset?: () => void
}) {
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  // A success message is stale once there are new edits; an error stays until the next try.
  const shown = message && (!message.ok || !dirty) ? message : null

  async function save() {
    try {
      await onSave()
      setMessage({ ok: true, text: 'Saved. New leads use it straight away.' })
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : "Couldn't save" })
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button onClick={() => void save()} busy={saving} disabled={!dirty}>
        Save changes
      </Button>
      {onReset && dirty ? (
        <Button variant="ghost" onClick={onReset}>
          Discard
        </Button>
      ) : null}
      {shown ? (
        <p
          role="status"
          className={shown.ok ? 'text-sm text-success' : 'text-sm text-priority-critical'}
        >
          {shown.text}
        </p>
      ) : null}
    </div>
  )
}
