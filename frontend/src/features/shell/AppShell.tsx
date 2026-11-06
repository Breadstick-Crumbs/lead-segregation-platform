import { Link, Navigate, Outlet, useLocation } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

import { Icon } from '../../components/Icon'
import { Button } from '../../components/ui'
import { cn } from '../../lib/cn'
import { initials } from '../../lib/format'
import { useSession } from '../auth/useSession'
import { NotificationBell } from '../notifications/NotificationBell'
import { useNotificationStream } from '../notifications/useNotificationStream'
import { Toasts } from '../notifications/Toasts'
import { useTheme } from './theme'

interface NavItem {
  to: '/' | '/settings'
  label: string
  adminOnly?: boolean
}

const NAV: NavItem[] = [
  { to: '/', label: 'Leads' },
  { to: '/settings', label: 'Settings', adminOnly: true },
]

export function AppShell() {
  const session = useSession()
  const location = useLocation()
  const [theme, toggleTheme] = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  const live = useNotificationStream(session.status === 'signed-in')

  useEffect(() => {
    if (!menuOpen) return
    const close = (event: KeyboardEvent) => event.key === 'Escape' && setMenuOpen(false)
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [menuOpen])

  if (session.status === 'loading') {
    return (
      <div className="grid min-h-screen place-items-center text-muted" aria-busy>
        Loading…
      </div>
    )
  }
  if (session.status === 'signed-out' || !session.user) {
    return <Navigate to="/login" search={{ redirect: location.href }} replace />
  }

  const items = NAV.filter((item) => !item.adminOnly || session.isAdmin)
  const isActive = (to: string) =>
    to === '/'
      ? location.pathname === '/' || location.pathname.startsWith('/leads')
      : location.pathname.startsWith(to)

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only z-50 rounded-control bg-accent px-4 py-2 text-white focus:not-sr-only focus:fixed focus:top-4 focus:left-4"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 px-4 pt-4">
        <nav
          aria-label="Main"
          className="mx-auto flex max-w-7xl items-center gap-3 rounded-card border border-border bg-surface/95 px-3 py-2 backdrop-blur"
        >
          <Link to="/" className="flex items-center gap-2 rounded-control px-2 py-1.5">
            <span className="grid size-8 place-items-center rounded-control bg-accent font-mono text-sm font-bold text-white dark:text-bg">
              T
            </span>
            <span className="hidden font-bold text-ink sm:block">Tawasol Leads</span>
          </Link>

          <ul className="mx-auto hidden items-center gap-1 md:flex">
            {items.map((item) => (
              <li key={item.to}>
                <Link
                  to={item.to}
                  className={cn(
                    'rounded-full px-4 py-2 text-[15px] transition-colors duration-300',
                    isActive(item.to)
                      ? 'bg-accent-tint font-semibold text-accent'
                      : 'text-body hover:text-ink',
                  )}
                  aria-current={isActive(item.to) ? 'page' : undefined}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>

          <div className="ml-auto flex items-center gap-1 md:ml-0">
            <span
              className={cn(
                'hidden items-center gap-1.5 px-2 font-mono text-[11px] tracking-[0.14em] uppercase sm:flex',
                live ? 'text-success' : 'text-muted',
              )}
              title={live ? 'Receiving alerts live' : 'Reconnecting to live alerts'}
            >
              <span
                className={cn(
                  'size-2 rounded-full',
                  live ? 'animate-[pulse_3s_ease-in-out_infinite] bg-success' : 'bg-muted',
                )}
              />
              {live ? 'Live' : 'Offline'}
            </span>
            <NotificationBell />
            <button
              type="button"
              onClick={toggleTheme}
              className="grid size-11 place-items-center rounded-control text-body hover:bg-plate hover:text-ink"
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            </button>
            <div className="hidden items-center gap-2 pl-2 md:flex">
              <span
                className="grid size-9 place-items-center rounded-full bg-plate text-sm font-semibold text-ink"
                title={`${session.user.full_name} · ${session.user.role === 'admin' ? 'Admin' : 'Sales rep'}`}
              >
                {initials(session.user.full_name)}
              </span>
              <Button
                variant="ghost"
                size="sm"
                icon="logout"
                onClick={() => void session.signOut()}
              >
                Sign out
              </Button>
            </div>
            <button
              type="button"
              className="grid size-11 place-items-center rounded-control text-ink hover:bg-plate md:hidden"
              aria-label="Open menu"
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              onClick={() => setMenuOpen(true)}
            >
              <Icon name="menu" />
            </button>
          </div>
        </nav>
      </header>

      {menuOpen ? (
        <div
          id="mobile-menu"
          role="dialog"
          aria-modal="true"
          aria-label="Menu"
          className="fixed inset-0 z-40 flex flex-col bg-accent px-6 pt-6 pb-10 text-white md:hidden dark:text-bg"
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-[11px] tracking-[0.14em] uppercase opacity-80">
              Menu
            </span>
            <button
              type="button"
              className="grid size-11 place-items-center rounded-control hover:bg-white/10"
              aria-label="Close menu"
              onClick={() => setMenuOpen(false)}
              autoFocus
            >
              <Icon name="close" />
            </button>
          </div>
          <ul className="mt-10 flex flex-col gap-2">
            {items.map((item) => (
              <li key={item.to}>
                <Link
                  to={item.to}
                  onClick={() => setMenuOpen(false)}
                  className="block py-2 text-4xl font-bold tracking-[-0.02em]"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-auto flex items-center justify-between border-t border-white/20 pt-6">
            <div>
              <p className="font-semibold">{session.user.full_name}</p>
              <p className="text-sm opacity-80">{session.user.email}</p>
            </div>
            <button
              type="button"
              className="flex min-h-11 items-center gap-2 rounded-control px-4 font-semibold hover:bg-white/10"
              onClick={() => void session.signOut()}
            >
              <Icon name="logout" size={18} /> Sign out
            </button>
          </div>
        </div>
      ) : null}

      <main id="main" className="mx-auto max-w-7xl px-4 pt-8 pb-16">
        <Outlet />
      </main>
      <Toasts />
    </div>
  )
}
