/**
 * Keeps the notification stream open while signed in. New notifications refresh the bell and
 * the lead views and show a toast. The stream ends when the access token expires (and on any
 * drop), so it refreshes the token and reconnects with backoff. Events missed while
 * disconnected aren't replayed: every (re)connect and `resync` reloads the list instead.
 */
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { getAccessToken, refreshAccessToken } from '../../lib/api-client'
import { readEventStream } from '../../lib/sse'
import { pushToast } from './toastStore'

const STREAM_URL = '/api/v1/notifications/stream'
const MAX_BACKOFF_MS = 30_000

function invalidateLiveData(queryClient: QueryClient) {
  void queryClient.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey[0]
      return (
        typeof key === 'string' &&
        (key.startsWith('/api/v1/notifications') || key.startsWith('/api/v1/leads'))
      )
    },
  })
}

export function useNotificationStream(enabled: boolean): boolean {
  const queryClient = useQueryClient()
  const [live, setLive] = useState(false)

  useEffect(() => {
    if (!enabled) return
    const abort = new AbortController()
    const retry = { delay: 1000 } // mutable across reconnects, not across renders

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const onAbort = () => {
          clearTimeout(timer)
          resolve()
        }
        const timer = setTimeout(() => {
          abort.signal.removeEventListener('abort', onAbort)
          resolve()
        }, ms)
        abort.signal.addEventListener('abort', onAbort, { once: true })
      })

    async function connect(): Promise<Response | null> {
      const open = (token: string | null) =>
        fetch(STREAM_URL, {
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: abort.signal,
        })
      let response = await open(getAccessToken())
      if (response.status === 401) response = await open(await refreshAccessToken())
      return response.ok ? response : null
    }

    void (async () => {
      while (!abort.signal.aborted) {
        try {
          const response = await connect()
          if (response) {
            retry.delay = 1000
            await readEventStream(response, (event) => {
              if (event.event === 'ready' || event.event === 'resync') {
                setLive(true)
                invalidateLiveData(queryClient)
              } else if (event.event === 'notification') {
                invalidateLiveData(queryClient)
                try {
                  const data = JSON.parse(event.data) as {
                    id: string
                    title: string
                    lead_id: string | null
                    kind: string
                  }
                  pushToast({
                    id: data.id,
                    title: data.title,
                    leadId: data.lead_id,
                    kind: data.kind,
                  })
                } catch {
                  // malformed payload: the reload above still shows it
                }
              } else if (event.event === 'expired') {
                void refreshAccessToken()
              }
            })
          }
        } catch {
          // network drop or abort; reconnect below
        }
        setLive(false)
        if (abort.signal.aborted) return
        await sleep(retry.delay)
        retry.delay = Math.min(retry.delay * 2, MAX_BACKOFF_MS)
      }
    })()

    return () => {
      abort.abort()
      setLive(false)
    }
  }, [enabled, queryClient])

  return live
}
