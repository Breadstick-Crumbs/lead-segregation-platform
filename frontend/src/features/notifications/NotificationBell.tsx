import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'

import {
  getListNotificationsQueryKey,
  useListNotifications,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  type NotificationRead,
} from '../../api/generated'
import { Icon } from '../../components/Icon'
import { Button, Eyebrow, Skeleton } from '../../components/ui'
import { cn } from '../../lib/cn'
import { timeAgo } from '../../lib/format'

const REASONS: Record<string, string> = {
  high_priority_lead: 'High priority',
  escalation: 'Critical',
  sla_breach: 'SLA breached',
  assignment: 'Assigned to you',
}

export function NotificationBell() {
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const queryClient = useQueryClient()
  const notifications = useListNotifications({ limit: 20 })
  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: getListNotificationsQueryKey().slice(0, 1) })
  const markOne = useMarkNotificationRead({ mutation: { onSuccess: refresh } })
  const markAll = useMarkAllNotificationsRead({ mutation: { onSuccess: refresh } })
  const unread = notifications.data?.unread ?? 0

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        button.current?.focus()
      }
    }
    const onClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (!panel.current?.contains(target) && !button.current?.contains(target)) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onClick)
    }
  }, [open])

  function openItem(item: NotificationRead) {
    if (!item.read_at) markOne.mutate({ notificationId: item.id })
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative grid size-11 place-items-center rounded-control text-body hover:bg-plate hover:text-ink"
        aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-expanded={open}
        aria-controls="notification-panel"
      >
        <Icon name="bell" />
        {unread ? (
          <span className="absolute top-1.5 right-1.5 grid min-w-5 place-items-center rounded-full bg-priority-critical px-1 font-mono text-[11px] font-bold text-white">
            {unread > 99 ? '99+' : unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          ref={panel}
          id="notification-panel"
          role="dialog"
          aria-label="Notifications"
          className="fixed inset-x-4 top-20 z-40 max-h-[70vh] overflow-auto rounded-card border border-border bg-surface sm:absolute sm:inset-x-auto sm:top-12 sm:right-0 sm:w-96"
        >
          <div className="sticky top-0 flex items-center justify-between border-b border-border bg-surface px-4 py-3">
            <Eyebrow>Notifications{unread ? ` · ${unread} unread` : ''}</Eyebrow>
            {unread ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => markAll.mutate()}
                busy={markAll.isPending}
              >
                Mark all read
              </Button>
            ) : null}
          </div>
          {notifications.isPending ? (
            <div className="flex flex-col gap-2 p-4">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : notifications.isError ? (
            <p className="p-4 text-sm text-muted">
              Couldn't load notifications. They'll appear when the connection is back.
            </p>
          ) : notifications.data.items.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted">
              Nothing yet. High-priority leads and escalations show up here.
            </p>
          ) : (
            <ul>
              {notifications.data.items.map((item) => (
                <li key={item.id} className="border-b border-border last:border-0">
                  {item.lead_id ? (
                    <Link
                      to="/leads/$leadId"
                      params={{ leadId: item.lead_id }}
                      onClick={() => openItem(item)}
                      className={cn(
                        'flex gap-3 px-4 py-3 hover:bg-plate',
                        !item.read_at && 'bg-accent-tint',
                      )}
                    >
                      <NotificationBody item={item} />
                    </Link>
                  ) : (
                    <div className="flex gap-3 px-4 py-3">
                      <NotificationBody item={item} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  )
}

function NotificationBody({ item }: { item: NotificationRead }) {
  return (
    <>
      <span
        className={cn(
          'mt-2 size-2 shrink-0 rounded-full',
          item.read_at ? 'bg-transparent' : 'bg-accent',
        )}
        aria-label={item.read_at ? undefined : 'Unread'}
      />
      <span className="min-w-0">
        <span className="block font-mono text-[11px] tracking-[0.14em] text-muted uppercase">
          {REASONS[item.kind] ?? item.kind} · {timeAgo(item.created_at)}
        </span>
        <span className="mt-0.5 block font-semibold text-ink">{item.title}</span>
        {item.body ? (
          <span dir="auto" className="mt-0.5 line-clamp-2 block text-sm text-body">
            {item.body}
          </span>
        ) : null}
      </span>
    </>
  )
}
