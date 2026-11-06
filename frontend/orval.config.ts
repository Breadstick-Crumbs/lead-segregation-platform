import { defineConfig } from 'orval'

export default defineConfig({
  api: {
    input: 'http://localhost:8000/openapi.json',
    output: {
      target: 'src/api/generated.ts',
      client: 'react-query',
      httpClient: 'fetch',
      clean: true,
    },
  },
})
