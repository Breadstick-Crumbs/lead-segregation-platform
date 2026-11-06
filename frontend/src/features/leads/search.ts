import type {
  Channel,
  LeadCategory,
  LeadSort,
  LeadStatus,
  ListLeadsParams,
  Priority,
} from '../../api/generated'
import { LEAD_CATEGORIES, LEAD_STATUSES, PRIORITIES } from '../../lib/format'

/** The leads table's filters, kept in the URL so a view can be shared or bookmarked. */
export interface LeadSearch {
  q?: string
  source?: Channel
  category?: LeadCategory
  priority?: Priority
  status?: LeadStatus
  owner?: string // "me", "unassigned" or a user id
  sort?: LeadSort
  page?: number
}

const SORTS: LeadSort[] = ['newest', 'score', 'activity']

function pick<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : undefined
}

export function parseLeadSearch(raw: Record<string, unknown>): LeadSearch {
  const page = Number(raw.page)
  const q = typeof raw.q === 'string' && raw.q.trim() ? raw.q.slice(0, 200) : undefined
  const owner = typeof raw.owner === 'string' && raw.owner ? raw.owner : undefined
  return {
    q,
    source: pick(raw.source, ['email', 'whatsapp'] as const),
    category: pick(raw.category, LEAD_CATEGORIES),
    priority: pick(raw.priority, PRIORITIES),
    status: pick(raw.status, LEAD_STATUSES),
    owner,
    sort: pick(raw.sort, SORTS),
    page: Number.isInteger(page) && page > 1 ? page : undefined,
  }
}

export const PAGE_SIZE = 20

export function toListParams(search: LeadSearch): ListLeadsParams {
  return {
    q: search.q,
    source: search.source,
    category: search.category ? [search.category] : undefined,
    priority: search.priority ? [search.priority] : undefined,
    status: search.status ? [search.status] : undefined,
    owner: search.owner,
    sort: search.sort ?? 'newest',
    page: search.page ?? 1,
    page_size: PAGE_SIZE,
  }
}

export function hasFilters(search: LeadSearch): boolean {
  return Boolean(
    search.q ||
    search.source ||
    search.category ||
    search.priority ||
    search.status ||
    search.owner ||
    search.page,
  )
}
