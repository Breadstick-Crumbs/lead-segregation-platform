import type { SVGProps } from 'react'

// A small hand-picked set, drawn on a 24px grid with 1.75px strokes (Lucide-style geometry).
const PATHS = {
  mail: 'M4 6h16v12H4z M4 7l8 6 8-6',
  whatsapp:
    'M5 19l1.2-3.6A7.5 7.5 0 1 1 9 18.6L5 19z M9.5 9.5c0 2.5 2.5 5 5 5l1-1.2-1.6-.9-.8.8c-.9-.4-1.9-1.4-2.3-2.3l.8-.8-.9-1.6-1.2 1z',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16z M10 20a2 2 0 0 0 4 0',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-4-4',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
  menu: 'M4 7h16 M4 12h16 M4 17h16',
  close: 'M6 6l12 12 M18 6L6 18',
  chevronLeft: 'M15 6l-6 6 6 6',
  chevronRight: 'M9 6l6 6-6 6',
  chevronDown: 'M6 9l6 6 6-6',
  logout: 'M15 4h4v16h-4 M10 8l-4 4 4 4 M6 12h10',
  send: 'M4 12l16-8-6 16-2.5-6.5L4 12z',
  note: 'M6 4h9l3 3v13H6z M9 10h6 M9 14h6',
  link: 'M10 14l4-4 M9 7l1.5-1.5a3.5 3.5 0 0 1 5 5L14 12 M10 12l-1.5 1.5a3.5 3.5 0 0 0 5 5L15 17',
  alert: 'M12 4l9 16H3z M12 10v4 M12 17h.01',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M5 20a7 7 0 0 1 14 0',
  plus: 'M12 5v14 M5 12h14',
  refresh: 'M20 11a8 8 0 0 0-14.5-4.5L4 8 M4 4v4h4 M4 13a8 8 0 0 0 14.5 4.5L20 16 M20 20v-4h-4',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
} as const

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  size = 20,
  title,
  ...props
}: { name: IconName; size?: number; title?: string } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
      {...props}
    >
      {title ? <title>{title}</title> : null}
      <path d={PATHS[name]} />
    </svg>
  )
}
