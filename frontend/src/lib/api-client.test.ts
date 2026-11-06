import {
  ApiError,
  apiFetch,
  getAccessToken,
  refreshAccessToken,
  setAccessToken,
} from './api-client'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  setAccessToken(null)
})

test('concurrent 401s share one refresh, then retry with the new token', async () => {
  setAccessToken('expired')
  let refreshes = 0
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url === '/api/v1/auth/refresh') {
      refreshes += 1
      await new Promise((resolve) => setTimeout(resolve, 10))
      return json(200, { access_token: 'fresh' })
    }
    const auth = new Headers(init?.headers).get('Authorization')
    return auth === 'Bearer fresh'
      ? json(200, { ok: url })
      : json(401, { error: { code: 'unauthenticated', message: 'expired' } })
  })

  const results = await Promise.all([
    apiFetch('/api/v1/leads'),
    apiFetch('/api/v1/leads/stats'),
    apiFetch('/api/v1/notifications'),
  ])

  // Refresh tokens rotate: two parallel refreshes would look like token theft to the backend.
  expect(refreshes).toBe(1)
  expect(results).toEqual([
    { ok: '/api/v1/leads' },
    { ok: '/api/v1/leads/stats' },
    { ok: '/api/v1/notifications' },
  ])
  expect(getAccessToken()).toBe('fresh')
  expect(fetchMock).toHaveBeenCalledTimes(7)
})

test('a failed refresh ends the session and surfaces the original 401', async () => {
  setAccessToken('expired')
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
    String(input) === '/api/v1/auth/refresh'
      ? json(401, { error: { code: 'unauthenticated', message: 'Session expired' } })
      : json(401, { error: { code: 'unauthenticated', message: 'Not authenticated' } }),
  )

  await expect(apiFetch('/api/v1/leads')).rejects.toMatchObject({
    status: 401,
    code: 'unauthenticated',
  })
  expect(getAccessToken()).toBeNull()
})

test('errors carry the backend message, including validation details', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(
      json(409, {
        error: {
          code: 'conflict',
          message: 'WhatsApp allows free-form replies only within 24 hours',
        },
      }),
    )
    .mockResolvedValueOnce(
      json(422, {
        detail: [{ loc: ['body', 'body'], msg: 'String should have at least 1 character' }],
      }),
    )

  const conflict = await apiFetch('/api/v1/leads/x/reply').catch((e: unknown) => e)
  const invalid = await apiFetch('/api/v1/leads/x/notes').catch((e: unknown) => e)

  expect(conflict).toBeInstanceOf(ApiError)
  expect((conflict as ApiError).message).toContain('24 hours')
  expect((invalid as ApiError).message).toBe('body: String should have at least 1 character')
})

test('refresh never runs twice at once even when called directly', async () => {
  let calls = 0
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    calls += 1
    return json(200, { access_token: `t${calls}` })
  })

  const [a, b] = await Promise.all([refreshAccessToken(), refreshAccessToken()])

  expect(calls).toBe(1)
  expect(a).toBe(b)
})

test('across tabs, refreshes take turns behind a Web Lock', async () => {
  // Two tabs sending the same rotating cookie at once look like token theft to the backend.
  const held: string[] = []
  const request = vi.fn(async (name: string, work: () => Promise<unknown>) => {
    held.push(name)
    return work()
  })
  vi.stubGlobal('navigator', { ...navigator, locks: { request } })
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(200, { access_token: 'next' }))

  await expect(refreshAccessToken()).resolves.toBe('next')

  expect(held).toEqual(['lead-platform-auth-refresh'])
  vi.unstubAllGlobals()
})
