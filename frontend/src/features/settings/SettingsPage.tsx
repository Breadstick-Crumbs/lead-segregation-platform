import { Navigate, useNavigate, useSearch } from '@tanstack/react-router'

import { PageHeader } from '../../components/ui'
import { cn } from '../../lib/cn'
import { useSession } from '../auth/useSession'
import { AlertSettings } from './AlertSettings'
import { IntegrationsPanel } from './IntegrationsPanel'
import { KeywordSettings } from './KeywordSettings'
import { MailboxSettings } from './MailboxSettings'
import { ScoringSettings } from './ScoringSettings'
import { SETTINGS_TABS } from './tabs'
import { TemplateSettings } from './TemplateSettings'
import { UserSettings } from './UserSettings'

export function SettingsPage() {
  const { isAdmin } = useSession()
  const { tab = 'scoring' } = useSearch({ from: '/app/settings' })
  const navigate = useNavigate({ from: '/settings' })
  if (!isAdmin) return <Navigate to="/" replace />

  return (
    <div className="flex flex-col gap-8">
      <PageHeader eyebrow="Admin" title="Settings" accent="how leads are handled" />
      <nav
        aria-label="Settings sections"
        className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1 [scrollbar-width:none]"
      >
        {SETTINGS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-current={tab === t.id ? 'page' : undefined}
            onClick={() => void navigate({ search: { tab: t.id } })}
            className={cn(
              'min-h-11 shrink-0 rounded-full px-4 text-[15px] transition-colors duration-300',
              tab === t.id
                ? 'bg-accent-tint font-semibold text-accent'
                : 'text-body hover:bg-plate hover:text-ink',
            )}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div id="settings-panel">
        {tab === 'scoring' ? <ScoringSettings /> : null}
        {tab === 'keywords' ? <KeywordSettings /> : null}
        {tab === 'replies' ? <TemplateSettings /> : null}
        {tab === 'alerts' ? <AlertSettings /> : null}
        {tab === 'mailboxes' ? <MailboxSettings /> : null}
        {tab === 'users' ? <UserSettings /> : null}
        {tab === 'integrations' ? <IntegrationsPanel /> : null}
      </div>
    </div>
  )
}
