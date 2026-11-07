import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { Providers } from './app/providers'
import { applyInitialTheme } from './features/shell/theme'
import './index.css'
import { initSentry, reportError } from './lib/sentry'
import { router } from './router'

const root = document.getElementById('root')
if (!root) throw new Error('Root element #root not found')
applyInitialTheme()
initSentry()

createRoot(root, {
  // Errors React caught in a boundary or couldn't handle; window errors and unhandled
  // rejections are reported by the SDK itself.
  onCaughtError: (error) => {
    reportError(error)
    console.error(error)
  },
  onUncaughtError: (error) => {
    reportError(error)
    console.error(error)
  },
}).render(
  <StrictMode>
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
)
