import { createContext, useContext } from 'react'

import type { UserRead } from '../../api/generated'

export type Status = 'loading' | 'signed-in' | 'signed-out'

export interface Session {
  status: Status
  user: UserRead | null
  isAdmin: boolean
  signIn: (email: string, password: string) => Promise<void>
  /** After the Microsoft redirect: the backend set the refresh cookie; exchange it. */
  completeExternalSignIn: () => Promise<boolean>
  signOut: () => Promise<void>
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession must be used inside <SessionProvider>')
  return session
}
