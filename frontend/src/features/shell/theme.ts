import { useCallback, useEffect, useState } from 'react'

type Theme = 'light' | 'dark'
const KEY = 'lead-platform.theme'

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null // private mode or blocked storage
  }
}

function preferred(): Theme {
  return (
    stored() ?? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  )
}

/** Before the first render, so pages outside the app shell (sign-in, an AI app's consent
 * page) match the user's theme too, without a light flash. */
export function applyInitialTheme(): void {
  document.documentElement.classList.toggle('dark', preferred() === 'dark')
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(preferred)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(KEY, next)
      } catch {
        // the choice just won't persist
      }
      return next
    })
  }, [])

  return [theme, toggle]
}
