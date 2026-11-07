import { Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'

import { AuthCallbackPage } from './features/auth/AuthCallbackPage'
import { LoginPage } from './features/auth/LoginPage'
import { ConnectPage } from './features/connect/ConnectPage'
import { ConsentPage } from './features/connect/ConsentPage'
import { LeadDetailPage } from './features/leads/LeadDetailPage'
import { LeadsPage } from './features/leads/LeadsPage'
import { REPORT_PERIODS, type ReportPeriod } from './features/reports/format'
import { ReportsPage } from './features/reports/ReportsPage'
import { parseLeadSearch } from './features/leads/search'
import { SettingsPage } from './features/settings/SettingsPage'
import { TryLeadPage } from './features/try/TryLeadPage'
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

// An MCP client's sign-in lands here; it handles its own sign-in redirect, without the shell.
const consentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/oauth/consent',
  component: ConsentPage,
  validateSearch: (raw: Record<string, unknown>): { request?: string } => ({
    request: typeof raw.request === 'string' ? raw.request : undefined,
  }),
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

const tryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/try',
  component: TryLeadPage,
})

const connectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/ai-apps',
  component: ConnectPage,
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
  consentRoute,
  appRoute.addChildren([
    leadsRoute,
    leadRoute,
    reportsRoute,
    tryRoute,
    connectRoute,
    settingsRoute,
  ]),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent', scrollRestoration: true })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
