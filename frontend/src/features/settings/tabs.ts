export const SETTINGS_TABS = [
  { id: 'scoring', label: 'Scoring' },
  { id: 'keywords', label: 'Keywords' },
  { id: 'replies', label: 'Auto-reply' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'mailboxes', label: 'Mailboxes' },
  { id: 'users', label: 'Users' },
  { id: 'integrations', label: 'Integrations' },
] as const

export type SettingsTab = (typeof SETTINGS_TABS)[number]['id']
