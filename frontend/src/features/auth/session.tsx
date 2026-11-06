/**
 * The signed-in user. On load the session is restored from the refresh cookie, so a reload
 * doesn't sign anyone out; the access token itself only ever lives in memory.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  login as loginRequest,
  logout as logoutRequest,
  me,
  type UserRead,
} from '../../api/generated'
import {
  getAccessToken,
  onAccessTokenChange,
  refreshAccessToken,
  setAccessToken,
} from '../../lib/api-client'
import { clearToasts } from '../notifications/toastStore'
import { SessionContext, type Session, type Status } from './useSession'

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<UserRead | null>(null)

  const loadUser = useCallback(async () => {
    const profile = await me()
    setUser(profile)
    setStatus('signed-in')
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const token = getAccessToken() ?? (await refreshAccessToken())
      if (cancelled) return
      if (!token) {
        setStatus('signed-out')
        return
      }
      try {
        await loadUser()
      } catch {
        if (!cancelled) setStatus('signed-out')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [loadUser])

  // A refresh that fails mid-session (expired or revoked) signs the user out everywhere.
  useEffect(
    () =>
      onAccessTokenChange((token) => {
        if (token === null) {
          setUser(null)
          setStatus('signed-out')
        }
      }),
    [],
  )

  const signIn = useCallback(
    async (email: string, password: string) => {
      const tokens = await loginRequest({ email, password })
      setAccessToken(tokens.access_token)
      await loadUser()
    },
    [loadUser],
  )

  const completeExternalSignIn = useCallback(async () => {
    const token = await refreshAccessToken()
    if (!token) return false
    await loadUser()
    return true
  }, [loadUser])

  const signOut = useCallback(async () => {
    try {
      await logoutRequest()
    } finally {
      setAccessToken(null)
      queryClient.clear()
      clearToasts() // the next person on this machine mustn't see them
    }
  }, [queryClient])

  const value = useMemo<Session>(
    () => ({
      status,
      user,
      isAdmin: user?.role === 'admin',
      signIn,
      completeExternalSignIn,
      signOut,
    }),
    [status, user, signIn, completeExternalSignIn, signOut],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
