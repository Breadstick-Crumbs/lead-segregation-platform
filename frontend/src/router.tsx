import { Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'

import { AuthCallbackPage } from './features/auth/AuthCallbackPage'
import { LoginPage } from './features/auth/LoginPage'
import { LeadDetailPage } from './features/leads/LeadDetailPage'
import { LeadsPage } from './features/leads/LeadsPage'
import { REPORT_PERIODS, type ReportPeriod } from './features/reports/format'
import { ReportsPage } from './features/reports/ReportsPage'
import { parseLeadSearch } from './features/leads/search'
import { SettingsPage } from './features/settings/SettingsPage'
import { SETTINGS_TABS, type SettingsTab } from './features/settings/tabs'
import { AppShell } from './features/shell/AppShell'

const rootRoute = createRootRoute({ component: Outlet })

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
  validateSearch: (raw: Record<string, unknown>): { sso_error?: string; redirect?: string } => ({
    sso_error: typeof raw.sso_error === 'string' ? raw.sso_error : undefined,
    redirect: typeof raw.redirect === 'string' ? raw.redirect : undefined,
  }),
})

const callbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth/callback',
  component: AuthCallbackPage,
})

// Everything behind sign-in shares the nav shell.
const appRoute = createRoute({ getParentRoute: () => rootRoute, id: 'app', component: AppShell })

const leadsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  component: LeadsPage,
  validateSearch: parseLeadSearch,
})

const leadRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/leads/$leadId',
  component: LeadDetailPage,
})

const reportsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/reports',
  component: ReportsPage,
  validateSearch: (raw: Record<string, unknown>): { days?: ReportPeriod } => {
    const days = Number(raw.days)
    return { days: REPORT_PERIODS.find((p) => p === days) }
  },
})

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  component: SettingsPage,
  validateSearch: (raw: Record<string, unknown>): { tab?: SettingsTab } => ({
    tab: SETTINGS_TABS.some((t) => t.id === raw.tab) ? (raw.tab as SettingsTab) : undefined,
  }),
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  callbackRoute,
  appRoute.addChildren([leadsRoute, leadRoute, reportsRoute, settingsRoute]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent', scrollRestoration: true })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
