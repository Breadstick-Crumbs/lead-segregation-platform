import { useQueryClient } from '@tanstack/react-query'

import { getListSettingsQueryKey, useListSettings, useSaveSetting } from '../../api/generated'

/** One settings document (e.g. "scoring.weights"), read from the list and saved whole. */
export function useSettingsDoc<T>(key: string) {
  const queryClient = useQueryClient()
  const all = useListSettings()
  const save = useSaveSetting({
    mutation: {
      onSuccess: () => void queryClient.invalidateQueries({ queryKey: getListSettingsQueryKey() }),
    },
  })
  const value = all.data?.find((doc) => doc.key === key)?.value as T | undefined
  return {
    value,
    isPending: all.isPending,
    error: all.error,
    retry: () => void all.refetch(),
    save: (next: T) => save.mutateAsync({ key, data: next as Record<string, unknown> }),
    saving: save.isPending,
  }
}
