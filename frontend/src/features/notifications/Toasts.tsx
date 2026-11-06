import { Link } from '@tanstack/react-router'
import { useEffect } from 'react'

import { Icon } from '../../components/Icon'
import { Eyebrow } from '../../components/ui'
import { dismissToast, useToasts, type Toast } from './toastStore'

const KIND_LABELS: Record<string, string> = {
  high_priority_lead: 'High-priority lead',
  escalation: 'Escalation',
  sla_breach: 'SLA breached',
  assignment: 'Assigned to you',
}
const SHOW_MS = 8000

function ToastCard({ toast }: { toast: Toast }) {
  useEffect(() => {
    const timer = setTimeout(() => dismissToast(toast.id), SHOW_MS)
    return () => clearTimeout(timer)
  }, [toast.id])

  return (
    <li className="flex animate-[toast-in_0.3s_ease-out] items-start gap-3 rounded-card border border-border bg-surface p-4">
      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full bg-accent-tint text-accent">
        <Icon name="bell" size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <Eyebrow>{KIND_LABELS[toast.kind] ?? 'Notification'}</Eyebrow>
        {toast.leadId ? (
          <Link
            to="/leads/$leadId"
            params={{ leadId: toast.leadId }}
            className="mt-1 block font-semibold text-ink hover:text-accent"
            onClick={() => dismissToast(toast.id)}
          >
            {toast.title}
          </Link>
        ) : (
          <p className="mt-1 font-semibold text-ink">{toast.title}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismissToast(toast.id)}
        className="grid size-11 shrink-0 place-items-center rounded-control text-muted hover:bg-plate"
        aria-label="Dismiss notification"
      >
        <Icon name="close" size={16} />
      </button>
    </li>
  )
}

export function Toasts() {
  const toasts = useToasts()
  return (
    <ol
      aria-live="polite"
      aria-label="New notifications"
      className="fixed right-4 bottom-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
    >
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} />
      ))}
    </ol>
  )
}
