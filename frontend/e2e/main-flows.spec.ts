import { expect, test } from '@playwright/test'

import { sendWhatsAppLead, signIn } from './helpers.ts'

test('signing out returns to the sign-in page and protects the dashboard', async ({ page }) => {
  await signIn(page)
  await expect(page.getByRole('link', { name: 'Leads', exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()

  await expect(page).toHaveURL(/\/login/)
  await page.goto('/reports')
  await expect(page).toHaveURL(/\/login\?redirect=/)
})

test('a deep link survives signing in', async ({ page }) => {
  await signIn(page, '/reports')

  await expect(page).toHaveURL(/\/reports/)
})

test('open a lead and change its status', async ({ page }) => {
  await signIn(page)
  const firstLead = page.locator('table tbody tr a').first()
  await expect(firstLead).toBeVisible()
  await firstLead.click()
  await expect(page).toHaveURL(/\/leads\//)
  await expect(page.getByRole('heading', { name: 'Conversation' })).toBeVisible()

  const status = page.getByRole('combobox', { name: /^Status/ })
  const next = (await status.textContent())?.includes('Qualified') ? 'Contacted' : 'Qualified'
  await status.click()
  await page.getByRole('option', { name: next }).click()
  await page.getByRole('button', { name: 'Save' }).click()

  await expect(page.getByRole('button', { name: 'Save' })).toBeHidden()
  await page.reload()
  await expect(page.getByRole('combobox', { name: /^Status/ })).toHaveText(next)
})

test('a new WhatsApp lead arrives and gets a reply', async ({ page, request }) => {
  const stamp = Date.now()
  const name = `E2E Customer ${stamp}`
  await sendWhatsAppLead(request, {
    name,
    phone: `9715${String(stamp).slice(-8)}`,
    text: 'Hello, I would like a quote for 25 seats.',
  })

  await signIn(page)
  const search = page.getByPlaceholder(/Search by name/)
  const lead = page.getByRole('link', { name })
  // The worker turns the stored webhook into a lead within a few seconds.
  await expect(async () => {
    await search.fill(name)
    await expect(lead).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 30_000 })
  await lead.click()

  await expect(page.getByText('Hello, I would like a quote for 25 seats.')).toBeVisible()
  const reply = `Thanks ${stamp}, a colleague will send the quote today.`
  await page.getByLabel(/Message to/).fill(reply)
  await page.getByRole('button', { name: 'Send reply' }).click()

  await expect(page.getByText(reply)).toBeVisible()
})

test('reports show every section', async ({ page }) => {
  await signIn(page, '/reports')

  for (const heading of [
    'Leads by source',
    'Categories',
    'Conversion funnel',
    'Time to first response',
    'High-value leads',
  ]) {
    await expect(page.getByRole('heading', { name: heading })).toBeVisible()
  }
  await expect(page.getByRole('region', { name: 'Summary' })).toBeVisible()
})

test('try a lead explains when OpenAI is not configured', async ({ page }) => {
  // The E2E stack runs without an OpenAI key, so this exercises the page and the error path.
  await signIn(page, '/try')
  await page.getByRole('button', { name: 'Sales email' }).click()
  await expect(page.getByLabel('Email address')).toHaveValue('omar.haddad@gulfbuild.ae')
  await page.getByRole('button', { name: 'Run the pipeline' }).click()

  await expect(page.getByText('OpenAI is not configured on this server')).toBeVisible()
})
