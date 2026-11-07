import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The API, plus the MCP endpoint and its OAuth server (backend app/mcp_server
    // PUBLIC_PATHS). Whole path segments (the query string is part of what's matched), so
    // SPA routes such as /oauth/consent stay with Vite.
    proxy: {
      '/api': 'http://localhost:8000',
      '^/(mcp|authorize|token|register|revoke)(\\?|$)': 'http://localhost:8000',
      '^/\\.well-known/oauth-': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
