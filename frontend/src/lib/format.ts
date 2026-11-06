import type { Channel, LeadCategory, LeadStatus, Priority } from '../api/generated'

export const CATEGORY_LABELS: Record<LeadCategory, string> = {
  sales_opportunity: 'Sales opportunity',
  demo_request: 'Demo request',
  partnership_inquiry: 'Partnership',
  support_request: 'Support',
  general_inquiry: 'General',
  spam: 'Spam',
}

export const STATUS_LABELS: Record<LeadStatus, string> = {
  new: 'New',
  contacted: 'Contacted',
  qualified: 'Qualified',
  converted: 'Converted',
  lost: 'Lost',
  spam: 'Spam',
}

export const PRIORITY_LABELS: Record<Priority, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export const CHANNEL_LABELS: Record<Channel, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
}

export const LEAD_STATUSES = Object.keys(STATUS_LABELS) as LeadStatus[]
export const LEAD_CATEGORIES = Object.keys(CATEGORY_LABELS) as LeadCategory[]
export const PRIORITIES = Object.keys(PRIORITY_LABELS) as Priority[]

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "just now", "12 min ago", "3 h ago", "2 days ago", then a date. */
export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const then = new Date(iso)
  const diff = now.getTime() - then.getTime()
  if (diff < MINUTE) return 'just now'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} min ago`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} h ago`
  if (diff < 7 * DAY) {
    const days = Math.floor(diff / DAY)
    return days === 1 ? 'yesterday' : `${days} days ago`
  }
  return then.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function percent(rate: number | null | undefined): string {
  return rate == null ? '—' : `${Math.round(rate * 100)}%`
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts[0]?.[0] ?? '?')
    .concat(parts.length > 1 ? (parts.at(-1)?.[0] ?? '') : '')
    .toUpperCase()
}

const PRIORITY_TEXT: Record<Priority, string> = {
  critical: 'text-priority-critical',
  high: 'text-priority-high',
  medium: 'text-priority-medium',
  low: 'text-priority-low',
}

/** The text-colour class for a priority. */
export function priorityText(priority: Priority | null | undefined): string {
  return priority ? PRIORITY_TEXT[priority] : 'text-muted'
}
