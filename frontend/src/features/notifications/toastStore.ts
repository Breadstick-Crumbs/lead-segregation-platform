import { useSyncExternalStore } from 'react'

export interface Toast {
  id: string
  title: string
  leadId: string | null
  kind: string
}

const MAX_TOASTS = 3
let toasts: Toast[] = []
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

export function pushToast(toast: Toast): void {
  if (toasts.some((t) => t.id === toast.id)) return
  toasts = [toast, ...toasts].slice(0, MAX_TOASTS)
  emit()
}

export function dismissToast(id: string): void {
  toasts = toasts.filter((t) => t.id !== id)
  emit()
}

export function clearToasts(): void {
  toasts = []
  emit()
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => toasts,
  )
}
