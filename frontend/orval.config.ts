import { defineConfig } from 'orval'

// `pnpm gen:api` exports the backend's OpenAPI document to openapi.json, then generates typed
// TanStack Query hooks from it. Every request goes through `apiFetch` (auth and refresh).
export default defineConfig({
  api: {
    input: './openapi.json',
    output: {
      target: 'src/api/generated.ts',
      client: 'react-query',
      httpClient: 'fetch',
      clean: true,
      override: {
        mutator: { path: 'src/lib/api-client.ts', name: 'apiFetch' },
        query: { signal: true },
        // Hooks resolve to the response body; failures throw ApiError (see src/lib/api-client.ts).
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
})
