import { createHmac } from 'node:crypto'

import { expect, type APIRequestContext, type Page } from '@playwright/test'

// The values docker-compose.e2e.yml gives the stack; override them to run against another.
export const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL ?? 'admin@tawasol.test',
  password: process.env.E2E_ADMIN_PASSWORD ?? 'e2e-admin-password',
}
const APP_SECRET = process.env.E2E_WHATSAPP_APP_SECRET ?? 'e2e-app-secret'
const PHONE_NUMBER_ID = process.env.E2E_WHATSAPP_PHONE_NUMBER_ID ?? '109876543210'

export async function signIn(page: Page, path = '/'): Promise<void> {
  await page.goto(path)
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel('Work email').fill(ADMIN.email)
  await page.getByLabel('Password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}

/** A WhatsApp message delivered the way Meta does: a signed webhook call. */
export async function sendWhatsAppLead(
  request: APIRequestContext,
  { name, text, phone }: { name: string; text: string; phone: string },
): Promise<void> {
  const body = JSON.stringify({
    object: 'whatsapp_business_account',
    entry: [
      {
        id: 'WABA_ID',
        changes: [
          {
            field: 'messages',
            value: {
              messaging_product: 'whatsapp',
              metadata: { display_phone_number: '15550001111', phone_number_id: PHONE_NUMBER_ID },
              contacts: [{ profile: { name }, wa_id: phone }],
              messages: [
                {
                  from: phone,
                  id: `wamid.e2e.${phone}`,
                  timestamp: String(Math.floor(Date.now() / 1000)),
                  type: 'text',
                  text: { body: text },
                },
              ],
            },
          },
        ],
      },
    ],
  })
  const signature = createHmac('sha256', APP_SECRET).update(body).digest('hex')
  const response = await request.post('/api/v1/webhooks/whatsapp', {
    data: body,
    headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': `sha256=${signature}` },
  })
  expect(response.status()).toBe(200)
}
