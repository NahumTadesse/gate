import { QueryClient } from '@tanstack/react-query'
import { isApiError } from '../api/client'

export function createQueryClient({ retry = true }: { retry?: boolean } = {}) {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Retrying a 4xx won't change the answer; only retry server errors
        // and network failures.
        retry: retry
          ? (count, error) => count < 2 && !(isApiError(error) && error.status < 500)
          : false,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  })
}
