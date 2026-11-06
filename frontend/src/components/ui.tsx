/**
 * The shared building blocks. Tokens come from src/index.css (docs/DESIGN.md): 12px controls,
 * 16px cards, 24px plates, no shadows, 1px borders, mono eyebrows.
 */
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'

import { cn } from '../lib/cn'
import { Icon, type IconName } from './Icon'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const BUTTON: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-white hover:brightness-110 dark:text-bg',
  secondary: 'border border-border bg-surface text-ink hover:bg-plate',
  ghost: 'text-body hover:bg-plate hover:text-ink',
  danger: 'border border-border bg-surface text-priority-critical hover:bg-plate',
}

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant
    icon?: IconName
    busy?: boolean
    size?: 'md' | 'sm'
  }
>(function Button(
  { variant = 'primary', icon, busy, size = 'md', className, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-control font-semibold transition-[filter,background-color] duration-150 ease-out disabled:cursor-not-allowed disabled:opacity-50',
        size === 'md' ? 'min-h-11 px-4 text-[15px]' : 'min-h-11 px-3 text-sm', // 44px targets either way
        BUTTON[variant],
        className,
      )}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {icon ? <Icon name={icon} size={18} /> : null}
      {busy ? 'Working…' : children}
    </button>
  )
})

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn('font-mono text-[11px] tracking-[0.14em] text-muted uppercase', className)}>
      {children}
    </p>
  )
}

export function Card({
  children,
  className,
  as: Tag = 'section',
}: {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'article' | 'aside'
}) {
  return (
    <Tag className={cn('rounded-card border border-border bg-surface p-6', className)}>
      {children}
    </Tag>
  )
}

/** Page title in two tones: the first line ink, the second the accent. */
export function PageHeader({
  eyebrow,
  title,
  accent,
  actions,
}: {
  eyebrow: string
  title: string
  accent: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <Eyebrow>{eyebrow}</Eyebrow>
        <h1 className="mt-2 text-4xl leading-[1.05] font-bold tracking-[-0.02em] text-ink sm:text-5xl">
          {title}
          <span className="block text-accent">{accent}</span>
        </h1>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-control bg-plate', className)} aria-hidden />
}

export function EmptyState({
  title,
  children,
  icon = 'sparkle',
}: {
  title: string
  children?: ReactNode
  icon?: IconName
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-card border border-dashed border-border px-6 py-12 text-center">
      <span className="grid size-11 place-items-center rounded-full bg-accent-tint text-accent">
        <Icon name={icon} />
      </span>
      <p className="font-semibold text-ink">{title}</p>
      {children ? <div className="max-w-md text-sm text-muted">{children}</div> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Something went wrong'
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-surface px-5 py-4"
    >
      <div className="flex items-center gap-3">
        <span className="text-priority-critical">
          <Icon name="alert" />
        </span>
        <div>
          <p className="font-semibold text-ink">Couldn't load this</p>
          <p className="text-sm text-muted">{message}</p>
        </div>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" icon="refresh" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  )
}

const CONTROL =
  'w-full rounded-control border border-border bg-surface px-3 text-[16px] text-ink placeholder:text-muted transition-colors duration-150 focus-visible:border-accent aria-[invalid=true]:border-priority-critical'

function FieldShell({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string
  label: string
  hint?: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-semibold text-ink">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-sm text-priority-critical">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

function describedBy(id: string, error?: string | null, hint?: string): string | undefined {
  if (error) return `${id}-error`
  return hint ? `${id}-hint` : undefined
}

export function TextField({
  label,
  hint,
  error,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  error?: string | null
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <input
        id={id}
        className={cn(CONTROL, 'min-h-11', className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        {...props}
      />
    </FieldShell>
  )
}

export function TextAreaField({
  label,
  hint,
  error,
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string
  hint?: string
  error?: string | null
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <textarea
        id={id}
        className={cn(CONTROL, 'min-h-24 py-2.5 leading-relaxed', className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        {...props}
      />
    </FieldShell>
  )
}

export function SelectField({
  label,
  hint,
  error,
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
  hint?: string
  error?: string | null
}) {
  const id = useId()
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <select
        id={id}
        className={cn(CONTROL, 'min-h-11 pr-8', className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, error, hint)}
        {...props}
      >
        {children}
      </select>
    </FieldShell>
  )
}

/** A tinted pill. `tone` picks the colour family; the text always says what it means. */
export function Chip({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'critical' | 'high' | 'medium' | 'low' | 'success'
  className?: string
}) {
  const tones = {
    neutral: 'bg-plate text-body',
    accent: 'bg-accent-tint text-accent',
    critical: 'bg-priority-critical/10 text-priority-critical',
    high: 'bg-priority-high/10 text-priority-high',
    medium: 'bg-priority-medium/10 text-priority-medium',
    low: 'bg-priority-low/10 text-priority-low',
    success: 'bg-success/10 text-success',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[13px] font-semibold whitespace-nowrap',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
